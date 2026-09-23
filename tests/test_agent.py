"""The agent layer: tool registry, memory, motion detection, and the
pipeline's call -> result -> answer loop.

No network, no camera, no API keys: the LLM, TTS and HTTP are all faked, so
this runs anywhere in about a second.

Run: .venv/Scripts/python.exe tests/test_agent.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.runtime import AgentRuntime
from backend.domain.memory.conversation import Conversation
from backend.domain.memory.store import MemoryStore
from backend.domain.vision.motion import MotionConfig, MotionDetector
from backend.core.tools import ToolContext, ToolRegistry, tool
from backend.core.tools.catalog import build_registry
from backend.domain.system import tools as system_tools
from backend.config import Settings
from backend.domain.brain.llm import GroqLlm, ToolCall


def run(coro):
    return asyncio.run(coro)


def settings(**kw) -> Settings:
    base = dict(gemini_api_key="", groq_api_key="x", camera_enabled=False)
    base.update(kw)
    return Settings(_env_file=None, **base)


def runtime(**kw) -> AgentRuntime:
    rt = AgentRuntime(settings(**kw))
    rt.memory = MemoryStore(Path(tempfile.mkdtemp()) / "memory.json")
    return rt


# -- registry ----------------------------------------------------------------

@tool("echo", "Echo text back.", params={"text": {"type": "string"}},
      required=["text"])
async def echo(ctx, text):
    return text


@tool("boom", "Always fails.")
async def boom(ctx):
    raise ValueError("kaboom")


@tool("slow", "Never finishes.")
async def slow(ctx):
    await asyncio.sleep(10)


@tool("launch", "Pretend to change the machine.", action=True)
async def launch(ctx):
    return "launched"


def registry(**kw) -> ToolRegistry:
    rt = runtime()
    reg = ToolRegistry(ToolContext(rt.settings, rt), **kw)
    reg.register_module(sys.modules[__name__])
    return reg


def test_tool_must_be_async():
    try:
        tool("x", "y")(lambda ctx: None)
    except TypeError:
        return
    raise AssertionError("sync tool accepted")


def test_schemas_for_both_brains():
    reg = registry()
    oa = {t["function"]["name"]: t for t in reg.openai_tools()}
    assert oa["echo"]["function"]["parameters"]["required"] == ["text"]
    decls = reg.gemini_tools()[0]["function_declarations"]
    by_name = {d["name"]: d for d in decls}
    assert by_name["echo"]["parameters_json_schema"]["properties"]["text"]
    assert "parameters_json_schema" not in by_name["boom"]  # no-arg tool


def test_json_string_args_and_unknown_params_dropped():
    out = run(registry().execute("echo", '{"text": "hi", "bogus": 1}'))
    assert out.ok and out.output == "hi"


def test_empty_args_string_is_empty_dict():
    out = run(registry().execute("echo", ""))
    assert not out.ok and "missing required" in out.output


def test_bad_json_is_an_error_not_a_crash():
    out = run(registry().execute("echo", "{not json"))
    assert not out.ok and "not valid JSON" in out.output


def test_exceptions_become_error_strings():
    out = run(registry().execute("boom", {}))
    assert not out.ok and "kaboom" in out.output


def test_timeout():
    out = run(registry(timeout_s=0.05).execute("slow", {}))
    assert not out.ok and "timed out" in out.output


def test_unknown_tool():
    out = run(registry().execute("nope", {}))
    assert not out.ok and "no tool named" in out.output


def test_results_truncated():
    out = run(registry(max_chars=10).execute("echo", {"text": "x" * 50}))
    assert out.output.startswith("x" * 10) and "truncated" in out.output


def test_action_tools_need_permission():
    assert "launch" not in registry(allow_actions=False)
    assert "launch" in registry(allow_actions=True)


def test_builtin_catalogue_respects_settings():
    rt = runtime()
    names = build_registry(rt.settings, rt).names
    assert {"get_datetime", "web_search", "remember", "set_timer"} <= set(names)
    # Camera is off in these settings, so camera tools must not be offered:
    # the model would promise to look and then fail.
    assert "look" not in names and "start_motion_watch" not in names

    rt = runtime(tools_enabled="get_datetime, recall")
    assert build_registry(rt.settings, rt).names == ["get_datetime", "recall"]
    rt = runtime(tools_enabled="")
    assert build_registry(rt.settings, rt).names == []


def test_open_app_only_runs_allowlisted_names():
    rt = runtime()
    reg = ToolRegistry(ToolContext(rt.settings, rt), allow_actions=True)
    reg.register_module(system_tools)
    out = run(reg.execute("open_app", {"name": "C:/Windows/System32/cmd.exe /c del"}))
    assert "not an allowed app" in out.output, out.output


def test_timer_publishes_event():
    async def go():
        rt = runtime()
        q = rt.events.subscribe()
        reg = build_registry(rt.settings, rt)
        out = await reg.execute("set_timer", {"seconds": 1, "label": "tea"})
        assert out.ok
        event = await asyncio.wait_for(q.get(), 3)
        assert event.kind == "timer" and "tea" in event.say
    run(go())


# -- memory ------------------------------------------------------------------

def test_memory_round_trip_and_forget():
    path = Path(tempfile.mkdtemp()) / "m.json"
    m = MemoryStore(path)
    m.add("The user's sister is called Asha.")
    m.add("The user takes coffee black.")
    m.add("the user's sister is called asha.")  # duplicate, ignored
    assert len(m) == 2
    assert MemoryStore(path).search("sister")[0].text.startswith("The user's sister")
    gone = m.forget("coffee")
    assert len(gone) == 1 and len(MemoryStore(path)) == 1


def test_memory_in_system_prompt():
    rt = runtime()
    rt.memory.add("The user's name is Nitish.")
    assert "Nitish" in rt.system_prompt()


def test_corrupt_memory_file_does_not_stop_startup():
    path = Path(tempfile.mkdtemp()) / "m.json"
    path.write_text("{ nope")
    assert len(MemoryStore(path)) == 0
    assert path.with_suffix(".corrupt.json").exists()


# -- motion ------------------------------------------------------------------

def test_motion_ignores_noise_and_catches_movement():
    rng = np.random.default_rng(0)
    det = MotionDetector(MotionConfig(warmup_frames=3, consecutive=2))
    scene = rng.integers(60, 90, (120, 160)).astype(np.uint8)

    def noisy(img):
        return np.clip(img + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)

    assert not any(det.update(noisy(scene)) for _ in range(20))

    moved = scene.copy()
    moved[30:90, 40:100] = 230  # something bright crosses ~19% of the frame
    hits = [det.update(noisy(moved)) for _ in range(3)]
    assert hits[-1], f"no motion detected, area {det.last_area:.3f}"


def test_motion_single_frame_flicker_ignored():
    det = MotionDetector(MotionConfig(warmup_frames=1, consecutive=3))
    dark = np.full((120, 160), 50, np.uint8)
    for _ in range(5):
        det.update(dark)
    assert not det.update(np.full((120, 160), 200, np.uint8))
    assert not det.update(dark)


# -- history -----------------------------------------------------------------

def test_trim_never_leaves_orphan_tool_results():
    c = Conversation("sys", max_turns=2)
    c.add_user("what time is it")
    c.add_tool_exchange(
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "type": "function",
                         "function": {"name": "get_datetime", "arguments": "{}"}}]},
        [{"role": "tool", "tool_call_id": "1", "content": "Monday 10:00"}])
    c.add_assistant("It's ten.")
    c.add_user("thanks")
    c.add_assistant("Any time.")
    assert c.messages[0]["role"] == "user"
    assert all(m["role"] != "tool" or i > 0 for i, m in enumerate(c.messages))


# -- groq tool-call streaming -------------------------------------------------

def _sse(*chunks) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def test_groq_tool_call_fragments_reassembled():
    body = _sse(
        {"choices": [{"delta": {"content": "Let me check."}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "web_", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "search", "arguments": '{"query":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": ' "weather"}'}}]}}]},
    )
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, content=body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            llm = GroqLlm(settings(), c)
            return [x async for x in llm.stream([], tools=[{"type": "function"}])]

    items = run(go())
    assert items[0] == "Let me check."
    assert items[1] == ToolCall("call_a", "web_search", '{"query": "weather"}')
    assert sent["tool_choice"] == "auto"


def test_groq_retries_without_tools_on_tool_use_failed():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append("tools" in payload)
        if "tools" in payload:
            return httpx.Response(400, json={"error": {"code": "tool_use_failed"}})
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "ok"}}]}))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return [x async for x in GroqLlm(settings(), c).stream([], tools=[{}])]

    assert run(go()) == ["ok"] and calls == [True, False]


# -- the pipeline's tool loop, end to end -------------------------------------

class FakeLlm:
    name = "fake"

    def __init__(self):
        self.requests = []

    async def stream(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        if messages[-1]["role"] != "tool":
            yield "One moment."
            yield ToolCall("c1", "get_datetime", "{}")
        else:
            yield f"It is {messages[-1]['content'].split(',')[0]}."


class FakeTts:
    name = "fake"

    async def synth(self, text):
        return np.zeros(2400, np.int16)


def test_pipeline_runs_tool_then_answers():
    from backend.domain.brain.pipeline import PipelineBrain

    sent = []

    async def send_json(m):
        sent.append(m)

    async def send_audio(b):
        pass

    async def go():
        brain = PipelineBrain(settings(), send_json, send_audio, runtime())
        brain.llm, brain.tts = FakeLlm(), FakeTts()
        assert await brain.announce("say the day")
        await brain.turn.task
        return brain

    brain = run(go())
    kinds = [m["t"] for m in sent]
    assert "tool_call" in kinds and "tool_result" in kinds, kinds
    assert kinds[-1] == "turn_complete"
    # Second request carries the call and its result; first offered tools.
    (first, tools1), (second, tools2) = brain.llm.requests
    assert tools1 and second[-1]["role"] == "tool"
    spoken = "".join(m["delta"] for m in sent if m["t"] == "assistant_delta")
    assert spoken.startswith("One moment. It is "), spoken
    roles = [m["role"] for m in brain.history.messages]
    assert roles == ["user", "assistant", "tool", "assistant"], roles
    assert brain.history.messages[-1]["content"].startswith("It is ")


# -- proactive events reach the user -----------------------------------------

def test_session_speaks_events_only_when_idle():
    from starlette.websockets import WebSocketState

    from backend.core.events import Event
    from backend.domain.voice.fsm import State
    from backend.domain.voice.session import Session

    class FakeWs:
        client_state = WebSocketState.CONNECTED

        def __init__(self):
            self.sent = []

        async def send_json(self, m):
            self.sent.append(m)

    class FakeBrain:
        handles_turn_detection = False

        def __init__(self):
            self.announced = []

        async def announce(self, prompt):
            self.announced.append(prompt)
            return True

    async def go():
        rt = runtime()
        ws = FakeWs()
        s = Session(ws, rt.settings, rt)
        s.brain = FakeBrain()
        task = asyncio.create_task(s._watch_events())
        await asyncio.sleep(0)

        rt.events.publish(Event("timer", say="tea is ready"))
        await asyncio.sleep(0.02)
        assert s.brain.announced == ["tea is ready"]
        assert s.fsm.is_(State.THINKING)

        # Busy now: the next alert is shown but must not talk over anyone.
        rt.events.publish(Event("motion", say="something moved"))
        await asyncio.sleep(0.02)
        assert s.brain.announced == ["tea is ready"]
        assert [m["kind"] for m in ws.sent if m["t"] == "event"] == ["timer", "motion"]
        task.cancel()

    run(go())


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
