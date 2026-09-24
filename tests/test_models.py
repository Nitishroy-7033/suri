"""The provider-agnostic model layer: config profiles, message conversion for
each provider, streaming parsers, and the fallback chain.

No network, no API keys: HTTP goes through httpx.MockTransport and the
fallback tests use fake models.

Run: .venv/Scripts/python.exe tests/test_models.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import ModelProfile, Settings
from backend.core.models import ChatModel, FallbackModel, ModelError, ModelHub, ToolCall, build_model
from backend.core.models.base import Usage
from backend.core.models.providers.anthropic import to_anthropic_messages
from backend.core.models.providers.gemini import to_gemini_contents, to_gemini_functions
from backend.core.models.providers.ollama import OllamaModel, to_ollama
from backend.core.models.providers.openai_compat import OpenAICompatModel, to_openai_message


def run(coro):
    return asyncio.run(coro)


def settings(**kw) -> Settings:
    base = dict(gemini_api_key="g", groq_api_key="x", camera_enabled=False)
    base.update(kw)
    return Settings(_env_file=None, **base)


TOOL = {"type": "function", "function": {
    "name": "web_search", "description": "Search.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}

HISTORY = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "weather?"},
    {"role": "assistant", "content": "Checking.", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "weather"}'},
         "meta": {"gemini_thought_signature": "c2ln"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "Sunny, 24C"},
]


# -- config -----------------------------------------------------------------------

def test_default_profiles_follow_legacy_settings():
    s = settings(groq_llm_model="m1", web_agent_model="wa", vision_model="vm")
    assert s.voice_llm.provider == "groq" and s.voice_llm.model == "m1"
    assert [p.provider for p in s.voice_llm.fallback] == ["groq", "ollama"]
    assert s.web_agent.provider == "gemini" and s.web_agent.model == "wa"
    assert s.vision_agent.model == "vm"
    assert s.diagnostics_agent.provider == "ollama"


def test_env_overrides_one_field_or_the_whole_profile():
    env = {"DIAGNOSTICS_AGENT__MODEL": "gemma4:e2b",
           "WEB_AGENT__PROVIDER": "openai", "WEB_AGENT__MODEL": "gpt-x",
           "WEB_AGENT__FALLBACK": '[{"provider":"anthropic","model":"claude-haiku-4-5"}]'}
    with mock.patch.dict(os.environ, env):
        s = Settings(_env_file=None)
    # Only the model changed; provider and tuning kept from the default.
    assert s.diagnostics_agent.provider == "ollama" and s.diagnostics_agent.model == "gemma4:e2b"
    assert s.diagnostics_agent.max_tokens == 120
    # A different provider replaces the default outright.
    assert s.web_agent.provider == "openai" and s.web_agent.model == "gpt-x"
    assert [(p.provider, p.model) for p in s.web_agent.fallback] == [("anthropic", "claude-haiku-4-5")]


def test_model_key_wins_over_provider_key():
    s = settings(groq_api_key="shared")
    m = build_model(ModelProfile(provider="groq", model="m", model_key="own"), s)
    assert m.key == "own"
    assert build_model(ModelProfile(provider="groq", model="m"), s).key == "shared"


def test_hub_flattens_fallbacks_and_filters_local():
    s = settings()
    hub = ModelHub(s)
    chain = hub.chain("voice_llm")
    assert [p.provider for p in chain] == ["groq", "groq", "ollama"]
    assert [p.provider for p in hub.chain("voice_llm", local_only=True)] == ["ollama"]
    assert hub.get("voice_llm") is hub.get("voice_llm")  # cached


def test_hub_reports_why_an_agent_cannot_run():
    s = settings(gemini_api_key="", groq_api_key="")
    hub = ModelHub(s)
    why = hub.available("vision_agent")
    assert why and "GEMINI_API_KEY" in why
    assert hub.available("diagnostics_agent") is None  # Ollama needs no key


# -- conversion -------------------------------------------------------------------

def test_openai_strips_meta_and_encodes_images():
    msg = to_openai_message(HISTORY[2])
    assert "meta" not in msg["tool_calls"][0]
    img = to_openai_message({"role": "user", "content": [
        {"type": "image", "data": b"\xff\xd8", "mime": "image/jpeg"}, {"type": "text", "text": "hi"}]})
    assert img["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_ollama_arguments_become_objects():
    out = to_ollama(HISTORY[2])
    assert out["tool_calls"][0]["function"]["arguments"] == {"query": "weather"}
    img = to_ollama({"role": "user", "content": [
        {"type": "image", "data": b"abc"}, {"type": "text", "text": "what"}]})
    assert img["content"] == "what" and img["images"] == ["YWJj"]


def test_gemini_contents_roles_names_and_signatures():
    contents = to_gemini_contents(HISTORY[1:])
    assert [c.role for c in contents] == ["user", "model", "user"]
    call = contents[1].parts[1].function_call
    assert call.name == "web_search" and call.args == {"query": "weather"}
    assert contents[1].parts[1].thought_signature == b"sig"
    resp = contents[2].parts[0].function_response
    assert resp.name == "web_search" and resp.response == {"result": "Sunny, 24C"}
    fns = to_gemini_functions([TOOL, {"type": "function", "function": {"name": "noargs", "parameters": {}}}])
    assert fns[0]["parameters_json_schema"]["required"] == ["query"]
    assert "parameters_json_schema" not in fns[1]


def test_anthropic_blocks():
    out = to_anthropic_messages(HISTORY[1:])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "web_search",
                                    "input": {"query": "weather"}}
    assert out[2]["content"][0]["type"] == "tool_result" and out[2]["content"][0]["tool_use_id"] == "c1"


# -- streaming parsers ---------------------------------------------------------------

def _sse(*chunks) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def test_openai_compat_stream_text_tools_usage():
    body = _sse(
        {"choices": [{"delta": {"content": "Hi"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "web_search", "arguments": '{"q'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '":1}'}}]}}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
    )
    sent = {}

    def handler(request):
        sent["url"] = str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, content=body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            m = OpenAICompatModel(ModelProfile(provider="openai", model="gpt-x"), settings(openai_api_key="k"), c)
            return [x async for x in m.stream([{"role": "user", "content": "hi"}], [TOOL], tool_choice="required")], m

    items, m = run(go())
    assert items == ["Hi", ToolCall("a", "web_search", '{"q":1}')]
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["body"]["tool_choice"] == "required" and sent["body"]["stream_options"]["include_usage"]
    assert m.last_usage.prompt_tokens == 12 and m.last_usage.completion_tokens == 7


def test_openai_compat_errors_are_typed():
    async def go(status):
        t = httpx.MockTransport(lambda r: httpx.Response(status, text="nope"))
        async with httpx.AsyncClient(transport=t) as c:
            m = OpenAICompatModel(ModelProfile(provider="groq", model="m"), settings(), c)
            try:
                [x async for x in m.stream([{"role": "user", "content": "hi"}])]
            except ModelError as exc:
                return exc

    assert run(go(503)).retryable
    assert not run(go(401)).retryable


def test_ollama_stream_reports_tokens_per_second():
    lines = [{"message": {"content": "Hel"}}, {"message": {"content": "lo"}},
             {"done": True, "eval_count": 40, "eval_duration": 2_000_000_000, "prompt_eval_count": 9}]
    body = "\n".join(json.dumps(x) for x in lines).encode()

    async def go():
        t = httpx.MockTransport(lambda r: httpx.Response(200, content=body))
        async with httpx.AsyncClient(transport=t) as c:
            m = OllamaModel(ModelProfile(provider="ollama", model="x"), settings(), c)
            return "".join([x async for x in m.stream([{"role": "user", "content": "hi"}])]), m.last_usage

    text, usage = run(go())
    assert text == "Hello" and usage.tok_per_s == 20.0 and usage.prompt_tokens == 9


def test_ollama_down_is_retryable():
    def refuse(request):
        raise httpx.ConnectError("refused")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as c:
            m = OllamaModel(ModelProfile(provider="ollama", model="x"), settings(), c)
            try:
                await m.complete([{"role": "user", "content": "hi"}])
            except ModelError as exc:
                return exc

    exc = run(go())
    assert exc.retryable and "not reachable" in str(exc)


# -- fallback ---------------------------------------------------------------------

class FakeModel(ChatModel):
    def __init__(self, name, errors=(), text="ok"):
        super().__init__(ModelProfile(provider="ollama", model=name), settings())
        self.errors = list(errors)
        self.text = text
        self.calls = 0

    async def stream(self, messages, tools=None, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        yield self.text
        self.last_usage = Usage(completion_tokens=1)


def test_fallback_moves_on_and_remembers_down(monkeypatch=None):
    import backend.core.models.fallback as fb

    fb.RETRY_DELAYS = (0.0, 0.0, 0.0)
    a = FakeModel("a", [ModelError("x", 503, "busy")] * 3)
    b = FakeModel("b", text="from b")
    seen = []
    chain = FallbackModel([a, b], "test", lambda m, u, e: seen.append((m.model, e is None)))
    r = run(chain.complete([{"role": "user", "content": "hi"}]))
    assert r.text == "from b" and a.calls == 3 and chain.model == "b"
    assert seen[-1] == ("b", True) and seen[0] == ("a", False)
    # a is marked down: the next call goes straight to b.
    r = run(chain.complete([{"role": "user", "content": "hi"}]))
    assert a.calls == 3 and b.calls == 2


def test_fallback_does_not_retry_bad_requests():
    a = FakeModel("a", [ModelError("x", 400, "bad schema")])
    b = FakeModel("b")
    chain = FallbackModel([a, b], "test")
    try:
        run(chain.complete([{"role": "user", "content": "hi"}]))
        raise AssertionError("should raise")
    except ModelError as exc:
        assert exc.status == 400 and b.calls == 0


def test_stream_falls_back_only_before_first_token():
    a = FakeModel("a", [ModelError("x", 0, "down")])
    b = FakeModel("b", text="hello")
    chain = FallbackModel([a, b], "test")

    async def go():
        return [x async for x in chain.stream([{"role": "user", "content": "hi"}])]

    assert run(go()) == ["hello"]


def test_daily_quota_detected():
    assert ModelError("gemini", 429, "Quota exceeded for requests per day").daily_quota
    assert not ModelError("gemini", 429, "per minute").daily_quota


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
