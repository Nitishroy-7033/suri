"""Hearing and speaking: everything on the real-time audio path.

    session.py   one WebSocket = one Session: wake -> VAD -> brain
    protocol.py  wire format (mirrored by hand in frontend/protocol.js)
    audio.py     ring buffer, chunking, levels
    wake.py      wake word ("jarvis")
    vad.py       voice activity, endpointing, barge-in
    fsm.py       conversation states
    text.py      clause splitting, speech cleanup
    stt.py       speech to text engines
    tts.py       text to speech engines

Nothing in here knows what a tool is -- it hands audio to a brain and
plays back what comes out.
"""
