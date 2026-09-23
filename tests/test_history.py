"""Conversation history and interruption truncation.

`truncate_to_heard` is the subtle one: get it wrong and Jarvis believes he
said things you never heard, so follow-ups reference content that never
reached you.

Run: .venv/Scripts/python.exe tests/test_history.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.domain.memory.conversation import Conversation, SpokenSpan, truncate_to_heard

R = 24000


def spans():
    # three clauses, one second each
    return [
        SpokenSpan(0, R, "Python is a language."),
        SpokenSpan(R, 2 * R, "It is used for scripting and web servers."),
        SpokenSpan(2 * R, 3 * R, "Many people find it easy to read."),
    ]


def test_nothing_played_means_nothing_heard():
    assert truncate_to_heard(spans(), 0) == ""


def test_whole_clauses_kept():
    out = truncate_to_heard(spans(), 2 * R)
    assert out.startswith("Python is a language.")
    assert "web servers." in out
    assert "easy to read" not in out


def test_partial_clause_truncated_by_words():
    out = truncate_to_heard(spans(), R + R // 2)  # halfway through clause 2
    assert out.startswith("Python is a language.")
    assert "It is used" in out
    assert "web servers" not in out, out


def test_everything_played():
    out = truncate_to_heard(spans(), 10 * R)
    assert "easy to read" in out


def test_tiny_playback_keeps_at_least_one_word():
    out = truncate_to_heard(spans(), 100)
    assert out == "Python", out


def test_empty_spans_safe():
    assert truncate_to_heard([], 5000) == ""


def test_interrupted_reply_is_marked():
    c = Conversation("sys")
    c.add_user("what is python")
    c.add_assistant("Python is a", interrupted=True)
    assert c.messages[-1]["content"].endswith("[interrupted by user]")


def test_consecutive_user_messages_merge():
    """Interrupting before Jarvis speaks leaves two user turns in a row,
    which some APIs reject outright."""
    c = Conversation("sys")
    c.add_user("what is python")
    c.add_user("actually, what is java")
    assert len(c.messages) == 1
    assert "java" in c.messages[0]["content"]
    assert c.messages[0]["role"] == "user"


def test_history_is_capped():
    c = Conversation("sys", max_turns=2)
    for i in range(6):
        c.add_user(f"q{i}")
        c.add_assistant(f"a{i}")
    assert len(c.messages) == 4
    assert c.messages[0]["content"] == "q4"


def test_stale_conversation_resets():
    c = Conversation("sys", ttl_seconds=0)
    c.add_user("what is python")
    c.last_activity -= 10
    c.add_user("and java?")
    assert len(c.messages) == 1, "old context should not leak in"


def test_for_llm_prepends_system_prompt():
    c = Conversation("you are jarvis")
    c.add_user("hi")
    msgs = c.for_llm()
    assert msgs[0] == {"role": "system", "content": "you are jarvis"}
    assert msgs[1]["role"] == "user"


def test_blank_messages_ignored():
    c = Conversation("sys")
    c.add_user("   ")
    c.add_assistant("")
    assert c.messages == []


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
