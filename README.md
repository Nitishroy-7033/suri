# Jarvis

A free, always-listening voice assistant. Say "Jarvis", ask a question, get a
spoken answer -- and interrupt it mid-sentence like you would a person.

Runs in your browser talking to a local Python backend. Nothing here costs
money: it uses free API tiers where they are fastest, and local models when
you want it fully offline.

## Status

**It talks.** Say "Jarvis, what is Python?" and you get a spoken answer in
about a second, in a voice that does not sound like a phone menu. You can
interrupt it mid-sentence.

Two brains, picked automatically:

| mode | what it is | first audio | when it runs |
|---|---|---|---|
| **gemini_live** | `gemini-3.8-live`, native speech-to-speech | **~0.9 s** | default |
| **pipeline** | Groq Whisper -> Groq qwen -> edge-tts | ~1.5-3.5 s | Gemini key missing or quota gone |

`JARVIS_MODE=auto` (the default) tries Gemini first and falls through to the
pipeline without the frontend noticing -- both speak the same wire format.
Force one with `JARVIS_MODE=pipeline`.

## Quick start

Double-click `run.bat`. It creates the virtualenv, installs dependencies,
and opens <http://127.0.0.1:8080>.

Or by hand:

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8080
```

No API keys are needed yet. Phase 2 onwards uses two, both free and neither
requiring a credit card:

| Key | Where to get it | Used for |
|-----|-----------------|----------|
| `GEMINI_API_KEY` | aistudio.google.com -> "Get API key" | realtime speech-to-speech |
| `GROQ_API_KEY` | console.groq.com -> API Keys | fast STT + LLM fallback |

Copy `.env.example` to `.env` and paste them in when you get there.

> On the free tiers, Google and Groq may use your conversations to improve
> their models. The offline mode (Phase 4) needs no key and sends nothing
> anywhere.

## Why it runs in a browser

`getUserMedia({ echoCancellation: true })` hands us Chrome's echo canceller
for free. Without it the microphone hears Jarvis through the laptop speakers
and he interrupts himself on every reply -- and doing echo cancellation
ourselves in Python on Windows is genuinely painful.

With it, you can interrupt him on open speakers with no headphones.

## Architecture

```
Browser                              Python backend
  mic  --16 kHz PCM16, 80 ms frames-->  wake word -> VAD -> brain
  speaker <--24 kHz PCM16, 240 ms-----  STT -> LLM -> TTS
              + JSON control
```

One resampler exists in the whole system and it is the browser's: the capture
context runs at 16 kHz and the playback context at 24 kHz, so Chrome does that
work natively and Python never pays for it.

```
backend/
  main.py              FastAPI app; builds the AgentRuntime once at startup
  config.py            every knob, from .env
  core/                shared plumbing, no capability-specific code
    runtime.py           owns memory, camera, event bus, models; hands out tool registries
    events.py            proactive alerts (timers, motion) -> spoken when idle
    models/              every agent's model, any provider (see "Models" below)
      base.py              ChatModel, ToolCall, Usage, ModelError
      providers/           openai_compat, gemini, ollama, anthropic
      fallback.py          primary + fallbacks, skips ones that are down
      hub.py               runtime.models.get("<agent>") from config profiles
    tools/
      base.py              Tool, ToolContext, @tool
      registry.py          schemas for both brains, safe execution
      catalog.py           which domains provide tools  <- register new ones here
  domain/              one folder per capability, everything it needs inside
    voice/               hearing + speaking (real-time, per connection)
      session.py protocol.py audio.py wake.py vad.py fsm.py text.py
      stt.py tts.py
    brain/               who thinks
      base.py factory.py gemini_live.py pipeline.py llm.py
    web/                 tools.py: web_search, read_webpage
    vision/              camera.py motion.py describe.py
                         tools.py: look, start/stop_motion_watch, camera_status
    memory/              conversation.py (short-term), store.py (long-term)
                         tools.py: remember, recall, forget
    system/              tools.py: get_datetime, set_timer, open_app, open_url
    diagnostics/         sampler.py alerts.py metrics.py agent.py
                         tools.py: system_status, unload_model
data/                  memory.json (created on first "remember")
```

### Models: one per agent, any provider

Each agent has its own model profile in `.env` -- provider, model name, key:

```
DIAGNOSTICS_AGENT__PROVIDER=ollama
DIAGNOSTICS_AGENT__MODEL=qwen2.5:1.5b
WEB_AGENT__PROVIDER=openai
WEB_AGENT__MODEL=gpt-5-mini
WEB_AGENT__MODEL_KEY=sk-...
WEB_AGENT__FALLBACK=[{"provider":"groq","model":"openai/gpt-oss-20b"}]
```

The agents are `voice_llm` (the pipeline brain), `web_agent`, `vision_agent`
and `diagnostics_agent`. Providers: `openai`, `groq`, `openrouter`,
`openai_compat` (any OpenAI-style server via `BASE_URL`: LM Studio, vLLM...),
`gemini`, `ollama`, `anthropic`. Set only what you want to change; the rest
keeps its default. A fallback runs when the one before it is rate-limited,
overloaded or down, and a model that failed that way is skipped for a few
minutes. Gemini Live, the realtime voice brain, is separate
(`GEMINI_LIVE_MODEL`).

In code, an agent asks `runtime.models.get("<agent>")` for its model and gets
the same `stream()` / `complete()` interface whatever the provider. To add a
provider, subclass `ChatModel` in `core/models/providers/` and add it to
`build_model` in `hub.py`.

### Diagnostics

The **Systems** view (the gauge in the layout switch, or `V`) shows the
laptop -- CPU and cores, RAM, GPU and VRAM, battery, disk, network, the
heaviest processes -- and Jarvis itself: each agent's model with tokens per
second and latency, the mic / STT / TTS, Ollama's loaded models, storage, and
recent warnings and errors. Stats only flow while the view is open.

"Jarvis, status report" calls `system_status`, which the diagnostics agent
answers with its own small model. Threshold alerts (battery, temperature,
RAM, VRAM, disk, network) are plain rules, so they cost nothing; the
diagnostics model only words them, and a fixed sentence is spoken if it is
unavailable. Other agents' words are never re-phrased by the voice: an
`Event(text=...)` is spoken as written.

Windows gives no CPU temperature without admin tools, so it shows "n/a". GPU
load and VRAM come from NVML on NVIDIA and from Windows' GPU performance
counters otherwise (no GPU temperature then).

### Adding a capability

Create `backend/domain/<name>/` with a `tools.py`:

```python
@tool("get_weather", "Current weather for a city.",
      params={"city": {"type": "string"}}, required=["city"])
async def get_weather(ctx: ToolContext, city: str) -> str:
    r = await ctx.runtime.http.get(f"https://wttr.in/{city}?format=3")
    return r.text
```

Then add that module to `DOMAIN_TOOL_MODULES` in `core/tools/catalog.py`.
Both brains pick it up. Nothing else changes. Keep results short: a model
about to speak two sentences reads them, and every kilobyte is latency.
Anything that changes the machine gets `action=True`, so
`TOOLS_ALLOW_ACTIONS=false` switches it off. Blocking work goes through
`asyncio.to_thread`.

Things that happen *unprompted* (a timer, motion) publish an `Event` on
`ctx.runtime.events`. Every session shows it, and speaks it only when IDLE,
so an alert never talks over the user.

`GET /api/tools` lists what the model is being offered right now.

### Tools, measured here

| question | brain | tool | time |
|---|---|---|---|
| "what time is it?" | pipeline (Groq) | get_datetime | 0.7 s to full answer |
| "who won the last F1 race?" | pipeline (Groq) | web_search | 2.1 s |
| "what time is it?" | gemini_live | get_datetime | 1.4 s to first audio |

Two things the live runs taught:

- **Models guess the date instead of asking.** Groq's qwen said "June 24" in
  September. Today's date now goes into the system prompt; the clock still
  goes through the tool.
- **Gemini ends the tool-calling turn before it answers.** It sends
  `turn_complete` the moment it issues a call, then answers in a *new*
  turn once it has the result. Passing that first one on would open the
  follow-up window before Jarvis had said a word, so it is swallowed.

### How a turn works

```
IDLE  --"hey jarvis"-->  LISTENING  --700ms silence-->  THINKING
  ^                                                        |
  |                                                        v
  +---6s of quiet--- FOLLOW_UP_WINDOW <---done--- SPEAKING
                            |
                     just talk again, no wake word
```

In IDLE only the wake word runs: no API calls, no audio leaving the machine.
It is switched off in every other state, because you may well say "Jarvis"
mid-sentence, and because the assistant saying its own name would otherwise
re-trigger itself through the speakers.

Capture is seeded with 500 ms of audio from *before* the wake fired. The wake
word only resolves at the end of "hey jarvis" and people run straight on into
the question, so without that pre-roll the first word is simply gone.

### Wake word

It responds to the **name**, not one fixed phrase. Measured on this machine:

| you say | score | | near-miss | score |
|---|---|---|---|---|
| "jarvis" | 0.995 | | "travis is coming over" | 0.025 |
| "hey jarvis" | 0.998 | | "the harvest festival" | 0.000 |
| "ok jarvis" | 0.999 | | "the service was good" | 0.000 |
| "hi jarvis" | 0.998 | | "explain what java is" | 0.007 |

With that much separation the threshold sits at **0.3**, which catches quiet
or distant speech that 0.5 was dropping.

### Measured here (Ryzen 7 5800U, CPU only, no GPU)

| component | cost | verdict |
|---|---|---|
| openWakeWord | 4.6 ms / 80 ms frame | inline |
| Silero VAD | 0.46 ms / 32 ms frame | inline |
| both together | ~7% of one core | no worker thread needed |
| model load per session | 224 ms | warmed at startup |
| Gemini Live connect | ~660 ms | session kept warm between turns |
| **Gemini Live, end of speech -> first audio** | **~0.9 s** | |
| Groq `whisper-large-v3-turbo` | 295 ms for 3.5 s of audio | fallback STT |
| Groq `qwen/qwen3.8-27b` | 313 ms, complete answer | fallback LLM |
| edge-tts | ~1.3 s to first chunk | fallback TTS, usable |
| **Kokoro-82M (int8, local)** | **RTF 2.0-3.3 -- slower than realtime** | **rejected** |

Kokoro was in the original plan as the local TTS. It is not viable on this
CPU: synthesising "Sure." takes 2.3 seconds. Threads do not help and int8 is
no faster than fp32 (Zen 3 has no AVX512-VNNI). It stays out until there is a
GPU, which is exactly why the plan called for measuring instead of trusting
the published RTF figures.

## Tests

```
.venv\Scripts\python.exe tests\test_audio.py      # ring buffer, framing, protocol
.venv\Scripts\python.exe tests\test_endpoint.py   # endpointing / noise floor
.venv\Scripts\python.exe tests\test_text.py       # clause splitting, speech cleanup
.venv\Scripts\python.exe tests\test_wake.py       # wake word + frame-skip regression
.venv\Scripts\python.exe tests\test_history.py    # interruption truncation
.venv\Scripts\python.exe tests\test_barge.py      # barge-in guards
.venv\Scripts\python.exe tests\test_agent.py      # tools, memory, motion, tool loop
.venv\Scripts\python.exe tests\test_models.py     # model profiles, providers, fallbacks
.venv\Scripts\python.exe tests\test_diagnostics.py # alert rules, sampler, status reports
.venv\Scripts\python.exe tests\probe_tools.py     # live: both brains call real tools
.venv\Scripts\python.exe tests\probe_phase0.py    # live: audio plumbing
.venv\Scripts\python.exe tests\probe_phase2.py    # live: a full spoken turn
.venv\Scripts\python.exe tests\probe_bargein.py   # live: interrupting mid-reply
node tests\xcheck_protocol.mjs <scratch-dir>      # JS/Python wire agreement
```

Both probes need the server running. `probe_phase1.py` streams real
synthesised speech (`tests/fixtures/`, generated with Windows SAPI) and
asserts the whole state machine: distractor phrases are ignored, "hey jarvis"
wakes it, the turn ends on silence rather than a timeout, the follow-up window
works without a wake word and then expires, and a wake with nothing said is
abandoned without spending anything.

`protocol.py` and `protocol.js` are mirrored by hand -- there is no codegen.
`xcheck_protocol.mjs` is what stops them drifting apart.

## Try it (needs your voice)

Open <http://127.0.0.1:8080>, click **Start mic**, then:

1. **"Jarvis, what is Python?"** -- spoken answer in about a second
2. **Interrupt it** halfway through -- it should stop and listen
3. Ask a follow-up with **no** wake word, within 6 seconds
4. Hindi: **"Jarvis, Python kya hai?"**
5. Talk normally for 5 minutes -- count false wakes

Tune by ear from the **Tuning** tab; it applies live, no restart. When a value
feels right, copy it into `.env`. Set `DEBUG_DUMP_AUDIO=true` to capture what
it heard as `debug/utt_*.wav`.

## Interrupting him

Barge-in works in both modes and is tested end to end
(`tests/probe_bargein.py`): measured **469 ms** in pipeline mode and
**676 ms** in Gemini mode from the moment you start talking over him to the
audio stopping.

In pipeline mode the decision is ours, and four guards all have to agree
before a reply is cut off:

| guard | why |
|---|---|
| 200 ms start guard | the browser's echo canceller needs to converge |
| 240 ms sustained speech | so a cough or an "mm-hm" does not stop him |
| level above -42 dBFS | room tone is not an interruption |
| 8 dB above the tracked noise floor | a fan does not slowly become "speech" |

In Gemini mode the model detects it server-side and we flush the browser's
queue on its signal.

Either way, the stored reply is trimmed to **what actually played** before
being written to history, with an `[interrupted by user]` marker. Without
that, Jarvis believes he said things you never heard and the next answer
makes no sense.

### One bug worth knowing about

The first version moved on as soon as it had *sent* the audio. Because the
brain can produce a 12-second reply in 2 seconds, the session left SPEAKING
about ten seconds before the user stopped hearing anything. Barge-in could
never fire, the follow-up window opened and expired mid-reply, and anything
you said while he was talking was taken as a new question.

The session now stays in SPEAKING until the browser reports its queue has
actually drained, with a fallback timer sized from the audio length in case
a report goes missing. Sending audio is not the same as someone hearing it.

## Why pipeline mode is slower

Measured per stage, on a real turn:

| stage | time |
|---|---|
| Groq Whisper (STT) | 231 ms |
| Groq qwen, first token | 99 ms |
| first clause ready for TTS | 107 ms |
| **edge-tts, first clause** | **900-3300 ms** |

Everything except TTS is essentially instant. edge-tts costs a fixed ~1-3 s
per request for the WebSocket handshake to Microsoft, almost regardless of
length -- "Sure." measured *slower* than a 57-character clause. Shortening
the first clause therefore does not help, which is why the clause splitter
does not try.

Clauses are synthesised **in parallel** and emitted in order. That does not
improve time-to-first-audio, but it stops a slow clause leaving a gap
mid-reply, and it cut total turn time from 9.5 s to 4.6 s for a 12-second
answer.

**The real fix is Groq's Orpheus TTS**, which would replace edge-tts with
something in the same few-hundred-millisecond class as Groq's other
endpoints. It needs a one-off terms acceptance:

> console.groq.com/playground?model=canopylabs%2Forpheus-v1-english

Once accepted, nothing else needs changing -- the pipeline probes for it on
startup and uses it automatically, logging `groq tts available`.

## If the wake word feels unreliable

The gauge shows the live score and a 4-second **peak**. Say "Jarvis" and read
the peak -- that number tells you what to do:

| peak | meaning | fix |
|---|---|---|
| 0.9+ | working | nothing |
| 0.4 - 0.9 | heard, but marginal | lower **Wake sensitivity** in the Tuning tab |
| 0.05 - 0.4 | barely registering | get closer, or raise mic volume in Windows |
| ~0.00 | not reaching the model | check the level meter moves at all |

If the level meter is flat, it is a microphone problem, not a wake word one:
check Windows input device and volume, and that the browser tab has mic
permission.

> A note on one bug worth remembering. An earlier build skipped wake-word
> inference on frames where VAD had heard nothing recently, to save ~6% of a
> core. openWakeWord keeps an internal audio buffer, so skipping frames
> splices discontinuous audio and the phrase arrives chopped up. Detection
> did not degrade -- it collapsed from **0.998 to 0.000**. The model now sees
> every frame unconditionally, and `tests/test_wake.py` has a regression test
> that fails if anyone reintroduces it.

## What is not done yet

- **Fast TTS in pipeline mode.** Blocked on the Orpheus terms acceptance
  above. edge-tts works meanwhile.
- **Acoustic barge-in on open speakers.** The logic is tested; what is not
  is whether the browser's echo canceller stops him hearing *himself*
  through your laptop speakers. That needs a real room.
- **Fully offline mode.** Blocked on local TTS: Kokoro is too slow on this
  CPU (see the table above). Piper is the likely substitute -- faster, more
  robotic. STT (`faster-whisper`) and LLM (Ollama) are already written.
- **Camera on real hardware.** `look` and motion watch are written and the
  detector is unit-tested, but neither has run against a physical webcam
  yet. Enable with `CAMERA_ENABLED=true` after
  `pip install opencv-python`.
- **Conversation history across restarts.** Long-term *facts* persist
  (`remember`), but the running conversation does not.
