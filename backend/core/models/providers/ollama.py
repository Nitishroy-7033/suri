"""Ollama's native /api/chat: local, no key, and it reports real timings.

The final `done` chunk carries eval_count / eval_duration, which is where the
HUD's tokens-per-second comes from. /api/ps and keep_alive=0 let the
diagnostics agent see and unload what is sitting in (V)RAM.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator

import httpx

from ..base import ChatModel, ModelError, ToolCall, ToolChoice, UsageClock, images_of, text_of


class OllamaModel(ChatModel):
    provider = "ollama"

    @property
    def host(self) -> str:
        return (self.profile.base_url or self.settings.ollama_host).rstrip("/")

    async def stream(self, messages, tools=None, *, tool_choice: ToolChoice = "auto",
                     temperature=None, max_tokens=None) -> AsyncIterator[str | ToolCall]:
        # Ollama has no tool_choice; "none" just means offer nothing.
        payload = {
            "model": self.model,
            "messages": [to_ollama(m) for m in messages],
            "stream": True,
            # Thinking models (gemma4, qwen3...) otherwise spend the whole
            # token budget reasoning and return nothing -- measured: 120
            # tokens, 40 s, empty reply. Set EXTRA={"think":true} to allow it.
            "think": False,
            "options": {
                "num_thread": self.settings.ollama_num_thread,
                "temperature": self.temperature(temperature),
                "num_predict": self.max_tokens(max_tokens),
            },
        }
        if tools and tool_choice != "none":
            payload["tools"] = tools
        payload.update(self.profile.extra)
        n_calls = 0
        clock = UsageClock()
        client = self.http or httpx.AsyncClient()
        try:
            async with client.stream("POST", f"{self.host}/api/chat", json=payload,
                                     timeout=self.timeout_s) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise ModelError(self.provider, resp.status_code, body)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = chunk.get("message") or {}
                    if message.get("content"):
                        clock.token()
                        yield message["content"]
                    # Ollama sends each tool call whole, with arguments as an
                    # object rather than JSON text.
                    for call in message.get("tool_calls") or []:
                        fn = call.get("function") or {}
                        n_calls += 1
                        clock.token()
                        yield ToolCall(id=f"call_{n_calls}", name=fn.get("name", ""),
                                       arguments=json.dumps(fn.get("arguments") or {}))
                    if chunk.get("done"):
                        clock.prompt_tokens = chunk.get("prompt_eval_count") or 0
                        clock.completion_tokens = chunk.get("eval_count") or 0
                        dur = chunk.get("eval_duration") or 0
                        if clock.completion_tokens and dur:
                            clock.tok_per_s = clock.completion_tokens / (dur / 1e9)
                        break
        except httpx.TimeoutException as exc:
            raise ModelError(self.provider, 0, str(exc) or "timed out", timeout=True) from exc
        except httpx.TransportError as exc:
            raise ModelError(self.provider, 0, f"Ollama not reachable at {self.host} "
                             f"({type(exc).__name__})") from exc
        finally:
            if self.http is None:
                await client.aclose()
        self.last_usage = clock.finish()


def to_ollama(m: dict) -> dict:
    """History is stored OpenAI-shaped; Ollama wants arguments as objects and
    images as a separate list of base64 strings."""
    content = m.get("content")
    if isinstance(content, list):
        out = {"role": m["role"], "content": text_of(content)}
        imgs = images_of(content)
        if imgs:
            out["images"] = [base64.b64encode(p["data"]).decode("ascii") for p in imgs]
        return out
    if m.get("role") != "assistant" or not m.get("tool_calls"):
        return m
    calls = []
    for c in m["tool_calls"]:
        fn = c["function"]
        try:
            args = json.loads(fn["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"function": {"name": fn["name"], "arguments": args}})
    return {"role": "assistant", "content": m.get("content") or "", "tool_calls": calls}


# -- model management (used by the diagnostics agent) --------------------------

async def ollama_ps(http: httpx.AsyncClient, host: str) -> dict:
    """What is loaded right now: {"reachable": bool, "models": [...]}."""
    try:
        r = await http.get(f"{host.rstrip('/')}/api/ps", timeout=1.5)
        r.raise_for_status()
    except (httpx.HTTPError, ValueError):
        return {"reachable": False, "models": []}
    models = []
    for m in r.json().get("models") or []:
        models.append({"name": m.get("name") or m.get("model"),
                       "size_mb": round((m.get("size") or 0) / 2**20),
                       "vram_mb": round((m.get("size_vram") or 0) / 2**20),
                       "expires_at": m.get("expires_at")})
    return {"reachable": True, "models": models}


async def ollama_unload(http: httpx.AsyncClient, host: str, model: str) -> None:
    r = await http.post(f"{host.rstrip('/')}/api/generate",
                        json={"model": model, "keep_alive": 0}, timeout=10)
    r.raise_for_status()


# -- model library (used by the Setup page) --------------------------------------

async def ollama_version(http: httpx.AsyncClient, host: str) -> str | None:
    """The server's version, or None when Ollama is not running there."""
    try:
        r = await http.get(f"{host.rstrip('/')}/api/version", timeout=1.5)
        r.raise_for_status()
        return r.json().get("version") or "?"
    except (httpx.HTTPError, ValueError):
        return None


async def ollama_tags(http: httpx.AsyncClient, host: str) -> list[dict]:
    """Every installed model, with what it can do (tools, vision, thinking)."""
    r = await http.get(f"{host.rstrip('/')}/api/tags", timeout=5)
    r.raise_for_status()
    out = []
    for m in r.json().get("models") or []:
        d = m.get("details") or {}
        out.append({"name": m.get("name") or m.get("model"),
                    "size_mb": round((m.get("size") or 0) / 2**20),
                    "modified_at": m.get("modified_at"),
                    "family": d.get("family"),
                    "params": d.get("parameter_size"),
                    "quant": d.get("quantization_level"),
                    "context": d.get("context_length"),
                    # Newer Ollama only; None means "unknown", not "none".
                    "capabilities": m.get("capabilities")})
    return out


async def ollama_delete(http: httpx.AsyncClient, host: str, model: str) -> None:
    r = await http.request("DELETE", f"{host.rstrip('/')}/api/delete",
                           json={"model": model}, timeout=30)
    if r.status_code >= 400:
        raise ModelError("ollama", r.status_code, r.text[:300])


async def ollama_load(http: httpx.AsyncClient, host: str, model: str,
                      num_thread: int, keep_alive: str = "10m") -> None:
    """Load a model into memory now, so the next call skips its cold start
    (15-20 s measured for a 5B model on this CPU).

    `num_thread` must match what OllamaModel sends: it is a runner option,
    and Ollama reloads the model when it differs -- measured, the next chat
    call paid a second 18 s load."""
    r = await http.post(f"{host.rstrip('/')}/api/generate",
                        json={"model": model, "keep_alive": keep_alive,
                              "options": {"num_thread": num_thread}}, timeout=180)
    if r.status_code >= 400:
        raise ModelError("ollama", r.status_code, r.text[:300])


async def ollama_pull(http: httpx.AsyncClient, host: str, model: str) -> AsyncIterator[dict]:
    """Download a model, yielding Ollama's progress lines as they come:
    {"status": "pulling <digest>", "digest", "total", "completed"}, ...,
    {"status": "success"}. Cancelling the iteration cancels the download."""
    # No read timeout worth the name: verifying a multi-GB layer can take
    # a minute with nothing sent.
    timeout = httpx.Timeout(15, read=600)
    async with http.stream("POST", f"{host.rstrip('/')}/api/pull",
                           json={"model": model, "stream": True}, timeout=timeout) as resp:
        if resp.status_code >= 400:
            body = (await resp.aread()).decode("utf-8", "replace")[:300]
            raise ModelError("ollama", resp.status_code, body)
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("error"):
                raise ModelError("ollama", 400, msg["error"])
            yield msg
