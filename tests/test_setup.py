"""The Setup page's backend: .env rewriting, agent profiles, live reload, and
the /api/setup routes (backend/setup_api.py).

No network and never the real .env: every route test points envfile at a
temp file, the runtime is a stub with its own Settings, and Ollama is an
httpx.MockTransport.

Run: .venv/Scripts/python.exe tests/test_setup.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values
from starlette.testclient import TestClient

from backend import envfile, setup_api
from backend.config import ModelProfile, Settings
from backend.core import security
from backend.core.models import FallbackModel, ModelHub
from backend.main import app

H = {"X-Jarvis-Token": security.TOKEN}


def tmp_env(text: str) -> Path:
    path = Path(tempfile.mkdtemp()) / ".env"
    path.write_text(text, encoding="utf-8", newline="")
    return path


# -- envfile ----------------------------------------------------------------------

def test_render_round_trips_awkward_values():
    values = {"A": "plain", "B": "has # hash", "C": " padded ", "D": "it's \"q\" \\ end\\",
              "E": "line1\nline2", "F": '[{"provider":"groq","model":"m"}]',
              "G": "C:\\path\\x", "H": "", "I": "a'b # c"}
    path = tmp_env(envfile.render("", values))
    parsed = dotenv_values(path)
    for k, v in values.items():
        assert parsed[k] == v, (k, parsed[k], v)


def test_render_keeps_layout_and_places_new_keys_near_relatives():
    src = ("# keys\r\nGEMINI_API_KEY=g\r\n\r\n# voice\r\nVOICE_LLM__PROVIDER=groq\r\n"
           "VOICE_LLM__MODEL=m\r\n# end\r\nOTHER=1\r\n")
    out = envfile.render(src, {"GROQ_API_KEY": "k", "VOICE_LLM__TEMPERATURE": "0.5",
                               "NEW_THING": "x"})
    lines = out.split("\r\n")
    assert lines[:3] == ["# keys", "GEMINI_API_KEY=g", "GROQ_API_KEY=k"]
    assert lines[lines.index("VOICE_LLM__MODEL=m") + 1] == "VOICE_LLM__TEMPERATURE=0.5"
    assert lines[-3:] == [envfile.MARKER, "NEW_THING=x", ""]
    assert "\n" not in out.replace("\r\n", "")  # CRLF kept throughout


def test_render_replaces_in_place_drops_duplicates_and_deletes():
    src = 'a=1\nDUP=1\nKEEP=1\nDUP=2\nMULTI="one\ntwo"\nGONE=x\n'
    out = envfile.render(src, {"A": "9", "DUP": "3", "MULTI": "m", "GONE": None})
    assert out == "A=9\nKEEP=1\nDUP=3\nMULTI=m\n"


def test_render_refuses_interpolation():
    try:
        envfile.render("", {"X": "${HOME}"})
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# -- profiles -> .env -> profiles --------------------------------------------------

def test_agent_changes_load_back_exactly():
    body = setup_api.AgentIn(provider="ollama", model="qwen2.5:1.5b", temperature=0.3,
                             max_tokens=90, extra={"think": True},
                             fallback=[setup_api.EntryIn(provider="groq", model="openai/gpt-oss-20b")])
    saved = [ModelProfile(provider="gemini", model="x", model_key="old-key")]
    changes = setup_api._agent_changes("diagnostics_agent", body, saved)
    assert changes["DIAGNOSTICS_AGENT__MODEL_KEY"] is None  # key was Gemini's; not carried over
    s = Settings(_env_file=tmp_env(envfile.render("", changes)))
    p = s.diagnostics_agent
    assert (p.provider, p.model, p.temperature, p.max_tokens, p.extra) == \
        ("ollama", "qwen2.5:1.5b", 0.3, 90, {"think": True})
    assert [(f.provider, f.model) for f in p.fallback] == [("groq", "openai/gpt-oss-20b")]


def test_empty_fallback_is_written_so_defaults_do_not_return():
    body = setup_api.AgentIn(provider="groq", model="m1")
    changes = setup_api._agent_changes("voice_llm", body, [ModelProfile(provider="groq", model="m0")])
    s = Settings(_env_file=tmp_env(envfile.render("", changes)), groq_api_key="x")
    assert s.voice_llm.model == "m1" and s.voice_llm.fallback == []


def test_fallback_keeps_fields_the_page_does_not_edit():
    saved = [ModelProfile(provider="groq", model="a"),
             ModelProfile(provider="groq", model="b", model_key="own", temperature=0.1)]
    body = setup_api.AgentIn(provider="groq", model="a",
                             fallback=[setup_api.EntryIn(provider="groq", model="b")])
    fb = json.loads(setup_api._agent_changes("voice_llm", body, saved)["VOICE_LLM__FALLBACK"])
    assert fb == [{"provider": "groq", "model": "b", "model_key": "own", "temperature": 0.1}]


# -- live reload -------------------------------------------------------------------

def test_reload_refills_the_same_chain_object():
    s = Settings(_env_file=None, groq_api_key="x", gemini_api_key="g")
    hub = ModelHub(s)
    fm = hub.get("vision_agent")
    assert fm.models[0].model == s.vision_agent.model
    hub.stats["vision_agent"].calls = 5
    s.vision_agent = ModelProfile(provider="ollama", model="moondream")
    assert hub.reload() == ["vision_agent"]
    assert hub.get("vision_agent") is fm  # holders keep working
    assert fm.models[0].describe() == "ollama:moondream"
    assert hub.stats["vision_agent"].calls == 0
    assert hub.reload() == []  # nothing changed, nothing rebuilt


def test_reload_rebuilds_when_only_the_key_changed():
    s = Settings(_env_file=None, gemini_api_key="old")
    hub = ModelHub(s)
    first = hub.get("vision_agent").models[0]
    s.gemini_api_key = "new"
    assert hub.reload() == ["vision_agent"]
    assert hub.get("vision_agent").models[0] is not first


def test_replaced_chain_ignores_a_late_failure_from_the_old_one():
    from backend.core.models import ModelError

    s = Settings(_env_file=None, groq_api_key="x")
    hub = ModelHub(s)
    fm = hub.get("voice_llm")
    old = fm.models[0]
    fm.replace(list(fm.models[1:]))
    fm._mark_down(0, old, ModelError("groq", 503, "busy"))
    assert fm._down_until == {}


# -- routes ------------------------------------------------------------------------

TAGS = {"models": [
    {"name": "gemma4:e2b", "size": 7 * 2**30, "details": {"parameter_size": "5.1B",
     "quantization_level": "Q4_K_M"}, "capabilities": ["completion", "vision", "tools"]},
    {"name": "deepseek-coder:6.7b", "size": 4 * 2**30, "details": {}, "capabilities": ["completion"]},
]}


def ollama_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/version":
        return httpx.Response(200, json={"version": "0.34.3"})
    if path == "/api/tags":
        return httpx.Response(200, json=TAGS)
    if path == "/api/ps":
        return httpx.Response(200, json={"models": [{"name": "gemma4:e2b", "size": 2**30}]})
    if path == "/api/delete":
        name = json.loads(request.content)["model"]
        TAGS["models"] = [m for m in TAGS["models"] if m["name"] != name]
        return httpx.Response(200)
    if path == "/api/pull":
        lines = [{"status": "pulling manifest"},
                 {"status": "pulling abc", "digest": "abc", "total": 100, "completed": 40},
                 {"status": "pulling abc", "digest": "abc", "total": 100, "completed": 100},
                 {"status": "success"}]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
    return httpx.Response(404)


class Runtime:
    """What setup_api touches, on its own Settings and a mock Ollama."""

    def __init__(self, s: Settings) -> None:
        self.settings = s
        self.models = ModelHub(s)
        self.diag = None
        self.jobs: set = set()

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        return task


class Client(TestClient):
    # Each TestClient call runs on a fresh event loop, so the hub's client
    # is made fresh for it too.
    def request(self, *a, **kw):
        app.state.agent.models._http = httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler))
        return super().request(*a, **kw)


def setup_client(env_text: str) -> tuple[Client, Runtime, Path]:
    path = tmp_env(env_text)
    envfile.ENV_PATH = path
    rt = Runtime(Settings(_env_file=path))
    app.state.agent = rt
    return Client(app, base_url="http://127.0.0.1:8080"), rt, path


ENV = ("# keys\nGEMINI_API_KEY=gemini-secret-1234\nGROQ_API_KEY=\n\n"
       "VISION_AGENT__PROVIDER=gemini\nVISION_AGENT__MODEL=gemini-flash-latest\n"
       "DIAG_ALERTS=true\n")


def test_routes_need_the_token():
    c, _, _ = setup_client(ENV)
    assert c.get("/api/setup/state").status_code == 401
    assert c.put("/api/setup/keys", json={"values": {"groq_api_key": "x"}}).status_code == 401


def test_state_never_contains_a_raw_key():
    c, _, _ = setup_client(ENV)
    r = c.get("/api/setup/state", headers=H)
    assert r.status_code == 200
    assert "gemini-secret-1234" not in r.text
    key = next(k for k in r.json()["keys"] if k["env"] == "GEMINI_API_KEY")
    assert key["set"] and key["hint"] == "…1234"


def test_saving_an_agent_writes_env_and_applies_live():
    c, rt, path = setup_client(ENV)
    fm = rt.models.get("vision_agent")
    r = c.put("/api/setup/agents/vision_agent", headers=H,
              json={"provider": "ollama", "model": "gemma4:e2b", "fallback": []})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["now"] == ["vision_agent"]
    env = dotenv_values(path)
    assert env["VISION_AGENT__PROVIDER"] == "ollama" and env["VISION_AGENT__MODEL"] == "gemma4:e2b"
    assert env["VISION_AGENT__FALLBACK"] == "[]"
    assert path.read_text(encoding="utf-8").startswith("# keys\n")  # comments kept
    assert rt.settings.vision_agent.model == "gemma4:e2b"
    assert fm.models[0].describe() == "ollama:gemma4:e2b"


def test_a_bad_value_is_refused_and_the_file_left_alone():
    c, rt, path = setup_client(ENV)
    before = path.read_text(encoding="utf-8")
    r = c.put("/api/setup/fields", headers=H, json={"values": {"diag_interval_s": "fast"}})
    assert r.status_code == 400 and "DIAG_INTERVAL_S" in r.text
    assert path.read_text(encoding="utf-8") == before
    r = c.put("/api/setup/fields", headers=H, json={"values": {"host": "0.0.0.0"}})
    assert r.status_code == 400  # not editable from the page at all


def test_fields_report_when_each_change_applies():
    c, rt, path = setup_client(ENV)
    r = c.put("/api/setup/fields", headers=H, json={"values": {
        "diag_alerts": False, "jarvis_mode": "pipeline", "camera_index": 2,
        "app_allowlist": '{"notepad": "notepad.exe"}'}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert sorted(res["now"]) == ["app_allowlist", "diag_alerts"]
    assert res["reconnect"] == ["jarvis_mode"] and res["restart"] == ["camera_index"]
    env = dotenv_values(path)
    assert env["DIAG_ALERTS"] == "false" and env["APP_ALLOWLIST"] == '{"notepad":"notepad.exe"}'
    assert rt.settings.app_allowlist == {"notepad": "notepad.exe"}


def test_keys_save_and_clear():
    c, rt, path = setup_client(ENV)
    r = c.put("/api/setup/keys", headers=H, json={"values": {"groq_api_key": " new-groq-key-9999 "}})
    assert r.status_code == 200 and rt.settings.groq_api_key == "new-groq-key-9999"
    r = c.put("/api/setup/keys", headers=H, json={"values": {"gemini_api_key": ""}})
    assert r.status_code == 200 and rt.settings.gemini_api_key == ""
    assert "GEMINI_API_KEY=\n" in path.read_text(encoding="utf-8")  # line kept, value gone
    assert c.put("/api/setup/keys", headers=H, json={"values": {"host": "x"}}).status_code == 400


def test_ollama_view_marks_usage_and_missing_models():
    c, rt, _ = setup_client(ENV + "DIAGNOSTICS_AGENT__PROVIDER=ollama\nDIAGNOSTICS_AGENT__MODEL=qwen2.5:1.5b\n"
                            "VOICE_LLM__PROVIDER=ollama\nVOICE_LLM__MODEL=gemma4:e2b\n")
    o = c.get("/api/setup/ollama", headers=H).json()
    assert o["reachable"] and o["version"] == "0.34.3"
    gemma = next(m for m in o["models"] if m["name"] == "gemma4:e2b")
    assert gemma["used_by"] == ["voice_llm"] and gemma["loaded"]
    assert {"name": "qwen2.5:1.5b", "used_by": ["diagnostics_agent"]} in o["missing"]
    assert next(s for s in o["suggested"] if s["name"] == "qwen2.5:1.5b")["installed"] is False


def test_ollama_pull_and_delete():
    async def go():
        s = Settings(_env_file=tmp_env(ENV))
        rt = Runtime(s)
        rt.models._http = httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler))
        job = setup_api.PullJob("qwen2.5:1.5b")
        await setup_api._run_pull(rt, job)
        return job

    job = asyncio.run(go())
    assert job.state == "done" and job.completed == job.total == 100
    c, _, _ = setup_client(ENV)
    o = c.post("/api/setup/ollama/delete", headers=H, json={"name": "deepseek-coder:6.7b"}).json()
    assert [m["name"] for m in o["models"]] == ["gemma4:e2b"]
    assert c.post("/api/setup/ollama/pull", headers=H, json={"name": "bad name; rm"}).status_code == 400


def test_models_route_lists_ollama_with_capabilities():
    c, _, _ = setup_client(ENV)
    models = c.get("/api/setup/models", headers=H, params={"provider": "ollama"}).json()["models"]
    assert models[0] == {"id": "gemma4:e2b", "note": "5.1B · Q4_K_M · 7.0 GB",
                         "tools": True, "vision": True}
    assert models[1]["tools"] is False


def test_errors_keep_only_the_providers_message():
    from backend.core.models import ModelError

    exc = ModelError("gemini", 503, "503 Service Unavailable. {'message': '{\n  \"error\": {\n"
                     "    \"code\": 503,\n    \"message\": \"This model is currently experiencing "
                     "high demand.\",\n    \"status\": \"UNAVAILABLE\"")
    assert setup_api._short(exc) == "gemini 503: This model is currently experiencing high demand."
    assert setup_api._short(ModelError("groq", 401, "Invalid API Key")) == "groq 401: Invalid API Key"
    # Cut mid-message by ModelError's 300-character limit: still the readable part.
    long = ModelError("gemini", 429, "429 Too Many Requests. {'message': '{\n \"error\": {\n \"code\": 429,\n"
                      " \"message\": \"You exceeded your current quota, please check your plan " + "x" * 300)
    assert setup_api._short(long).startswith("gemini 429: You exceeded your current quota")


def test_load_sends_the_thread_count_chat_uses():
    # Ollama reloads a model whose runner options differ, so a load without
    # num_thread was followed by a second full load on the first chat call.
    from backend.core.models.providers import ollama as ollama_api

    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await ollama_api.ollama_load(http, "http://x", "m", 6)

    asyncio.run(go())
    assert seen["options"] == {"num_thread": 6}


def test_config_comments_become_hints():
    assert "jarvis" in setup_api.NOTES["wake_threshold"].lower()
    assert setup_api.GROUPS["wake_threshold"] == "wake word"


if __name__ == "__main__":
    real_env = envfile.ENV_PATH
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as exc:
                failed += 1
                print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    finally:
        envfile.ENV_PATH = real_env
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
