"""One folder per capability, each holding everything that capability needs.

    voice/    hearing and speaking: session, wake word, VAD, STT, TTS
    brain/    who thinks: Gemini Live, the STT->LLM->TTS pipeline, the LLM
    web/      web search and page reading
    vision/   camera, motion detection, describing what the camera sees
    memory/   conversation history and long-term facts
    system/   clock, timers, launching apps and URLs

A domain that the model can use exposes `tools.py`, registered in
core/tools/catalog.py.
"""
