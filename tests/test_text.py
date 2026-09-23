"""Clause splitting and speech-text cleanup.

The splitter decides how fast Jarvis starts talking, and the cleanup decides
whether he reads markdown out loud. Both are cheap to get subtly wrong.

Run: .venv/Scripts/python.exe tests/test_text.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domain.voice.text import (ClauseSplitter, is_stop_phrase, strip_wake_word,
                          text_for_speech)


def split_all(text, **kw):
    """Feed text one character at a time, as a token stream would arrive."""
    s = ClauseSplitter(**kw)
    out = []
    for ch in text:
        out.extend(s.feed(ch))
    out.extend(s.flush())
    return out


# ---------- wake word stripping ----------

def test_strips_every_wake_variant():
    for said in ["jarvis, what is python", "hey jarvis what is python",
                 "Hey Jarvis, what is python", "ok jarvis - what is python",
                 "okay jarvis. what is python", "hi jarvis, what is python",
                 "yo jarvis what is python", "  Jarvis:  what is python"]:
        assert strip_wake_word(said) == "what is python", said


def test_keeps_jarvis_when_not_a_prefix():
    assert strip_wake_word("who is jarvis") == "who is jarvis"
    assert strip_wake_word("tell me about jarvis") == "tell me about jarvis"


def test_strips_only_once():
    assert strip_wake_word("jarvis jarvis is a name") == "jarvis is a name"


# ---------- stop phrases ----------

def test_stop_phrases_detected():
    for p in ["stop", "Stop.", "shut up", "never mind", "that's all", "QUIET"]:
        assert is_stop_phrase(p), p


def test_normal_speech_is_not_a_stop_phrase():
    for p in ["stop the car please and tell me why", "what is python",
              "can you stop explaining recursion for a moment"]:
        assert not is_stop_phrase(p), p


# ---------- speech cleanup ----------

def test_markdown_is_removed():
    out = text_for_speech("**Python** is a `language`.\n\n- fast\n- simple")
    assert "*" not in out and "`" not in out and "-" not in out
    assert "Python is a language" in out


def test_code_fence_becomes_a_phrase():
    out = text_for_speech("Try this:\n```python\nprint('hi')\n```\nThat works.")
    assert "print" not in out and "code block" in out


def test_urls_and_symbols_spoken():
    assert "a link" in text_for_speech("see https://example.com/x for more")
    assert "percent" in text_for_speech("it grew 50%")
    assert " and " in text_for_speech("Tom & Jerry")


def test_headings_and_quotes_stripped():
    assert text_for_speech("## Title\n> quoted line") == "Title quoted line"


# ---------- clause splitting ----------

def test_first_chunk_is_small_and_early():
    parts = split_all("Python is a programming language that is widely used "
                      "for many things.")
    assert len(parts[0]) <= 60, parts[0]
    assert parts[0].startswith("Python is a")


def test_nothing_is_lost_or_duplicated():
    text = ("Python is a language. It is used for scripting, data work and "
            "web servers. Many people like it because it reads cleanly.")
    joined = " ".join(split_all(text))
    assert joined.replace("  ", " ") == text


def test_decimals_do_not_split():
    parts = split_all("The value is 3.5 and the other is 12.75 exactly.",
                      first_max_chars=200, min_chars=10)
    assert not any(p.endswith("3.") for p in parts), parts


def test_abbreviations_do_not_split():
    parts = split_all("Dr. Smith met Mr. Jones at 5.", first_max_chars=200,
                      min_chars=5)
    assert not any(p.strip().endswith("Dr.") for p in parts), parts
    assert not any(p.strip().endswith("Mr.") for p in parts), parts


def test_long_run_on_is_hard_split():
    text = "word " * 120
    parts = split_all(text)
    assert all(len(p) <= 210 for p in parts), [len(p) for p in parts]
    assert len(parts) > 2


def test_flush_emits_trailing_text_without_punctuation():
    s = ClauseSplitter()
    out = list(s.feed("Just a fragment"))
    out += list(s.flush())
    assert "".join(out).strip() == "Just a fragment"


def test_empty_stream_yields_nothing():
    assert split_all("") == []


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
