"""Who thinks. Both brains share one interface, so the session can fall
back from one to the other without the frontend noticing.

    base.py         the Brain interface
    factory.py      picks a brain (auto / gemini_live / pipeline), with fallback
    gemini_live.py  native speech-to-speech, native tool calls
    pipeline.py     STT -> LLM (+ tool rounds) -> TTS
    llm.py          streaming chat engines (Groq, Ollama), incl. tool calls
"""
