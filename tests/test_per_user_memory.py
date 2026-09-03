"""
Memory is per person, not per stream.

    python tests/test_per_user_memory.py

History used to be one shared buffer. That was fine while the streamer was the
only chatter and wrong the moment he was not:

  * A reply to one viewer carried the last five messages from EVERYONE as one
    conversation, so she answered person C mid-thread with person A.
  * `get_user_note_compression_prompt` built from that shared buffer and wrote
    the result to whoever was active at the trigger. Five viewers talking meant
    the fifth one's notes were written from a transcript of all five — she
    would remember other people's personalities as theirs, permanently, since
    notes persist to disk.

The second one is the reason this was worth fixing before voice: notes are the
only part of her memory that survives a restart, so a wrong one is wrong
forever.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persona.memory import (                                   # noqa: E402
    ANON, MAX_HISTORY, MAX_TRACKED_USERS, MemoryManager,
)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def fresh() -> MemoryManager:
    m = MemoryManager()
    m.histories.clear()
    m.exchange_counts.clear()
    m.recent_openers.clear()
    m.recent_game_lines.clear()
    m.user_notes.clear()
    return m


def test_threads_do_not_mix():
    print("\n--- one thread per person ---")
    m = fresh()
    m.add_exchange("what champion is that", "Riven.", "chat", "alice")
    m.add_exchange("privet", "Hm.", "chat", "boris")
    m.add_exchange("is she good", "Depends who is holding her.", "chat", "alice")

    alice = [e["content"] for e in m.get_history("chat", "alice")]
    boris = [e["content"] for e in m.get_history("chat", "boris")]

    check("alice sees her own two exchanges", len(alice) == 4, str(len(alice)))
    check("and nothing of boris's", "privet" not in alice, str(alice))
    check("boris sees only his own", boris == ["privet", "Hm."], str(boris))
    check("an unknown viewer starts empty",
          m.get_history("chat", "carol") == [])
    check("names are matched case-insensitively",
          m.get_history("chat", "ALICE") == m.get_history("chat", "alice"))


def test_notes_are_written_from_one_persons_words():
    print("\n--- notes come from that person only ---")
    m = fresh()
    m.add_exchange("i main support", "Someone has to.", "chat", "alice")
    m.add_exchange("i play only jungle and i flame", "Charming.", "chat", "boris")
    m.add_exchange("do you like aram", "It has its moments.", "chat", "alice")

    prompt = m.get_user_note_compression_prompt("alice")
    check("alice's own words are in her prompt", "i main support" in prompt)
    check("boris's words are NOT in her prompt",
          "only jungle" not in prompt, prompt)
    check("her replies to alice are there", "has its moments" in prompt)
    check("the prompt names her", "alice" in prompt.lower())

    boris_prompt = m.get_user_note_compression_prompt("boris")
    check("boris's prompt has his words", "only jungle" in boris_prompt)
    check("and not alice's", "i main support" not in boris_prompt)

    check("a viewer with no history says so",
          "No recent interaction" in m.get_user_note_compression_prompt("carol"))


def test_note_trigger_is_per_person():
    print("\n--- notes trigger when THEY have said enough ---")
    m = fresh()
    for i in range(MAX_HISTORY):
        m.add_exchange(f"alice {i}", "Hm.", "chat", "alice")
    for i in range(2):
        m.add_exchange(f"boris {i}", "Hm.", "chat", "boris")

    check("alice has reached the threshold", m.needs_compression("alice"))
    check("boris has not", not m.needs_compression("boris"))
    check("a silent viewer has not", not m.needs_compression("carol"))


def test_compression_clears_only_that_person():
    print("\n--- compressing one person leaves the others alone ---")
    m = fresh()
    for i in range(MAX_HISTORY):
        m.add_exchange(f"alice {i}", "Hm.", "chat", "alice")
    m.add_exchange("boris here", "Noted.", "chat", "boris")

    m.apply_compression("Alice asked about supports.", active_user="alice")

    check("the summary is stored",
          "Alice asked about supports" in m.general_memory, m.general_memory)
    check("alice's thread is cleared", m.get_history("chat", "alice") == [])
    check("her counter is reset", not m.needs_compression("alice"))
    check("boris keeps his thread",
          len(m.get_history("chat", "boris")) == 2,
          str(m.get_history("chat", "boris")))


def test_stream_summary_still_spans_everybody():
    print("\n--- the stream summary is a property of the room ---")
    m = fresh()
    m.add_exchange("i main support", "Someone has to.", "chat", "alice")
    m.add_exchange("only jungle", "Charming.", "chat", "boris")

    payload = m.get_compression_payload()
    check("both people appear in the stream summary payload",
          "i main support" in payload and "only jungle" in payload)
    check("and each line is attributed",
          "alice:" in payload and "boris:" in payload, payload[:200])


def test_unidentified_speakers_share_one_buffer():
    print("\n--- unattributed speech does not join someone's thread ---")
    m = fresh()
    m.add_exchange("alice speaking", "Hm.", "chat", "alice")
    m.add_exchange("who said that", "Nobody knows.", "voice", "")

    check("the anonymous buffer exists", ANON in m.histories)
    check("it did not land in alice's thread",
          "who said that" not in
          [e["content"] for e in m.get_history("chat", "alice")])
    check("and is readable on its own",
          "who said that" in
          [e["content"] for e in m.get_history("voice", "")])


def test_tracked_users_are_bounded():
    print("\n--- the buffer count is bounded ---")
    m = fresh()
    for i in range(MAX_TRACKED_USERS + 8):
        m.add_exchange(f"hello {i}", "Hm.", "chat", f"viewer{i}")

    check(f"at most {MAX_TRACKED_USERS} people are tracked",
          len(m.histories) == MAX_TRACKED_USERS, str(len(m.histories)))
    check("the earliest viewer was evicted",
          m.get_history("chat", "viewer0") == [])
    check("the most recent is kept",
          len(m.get_history("chat", f"viewer{MAX_TRACKED_USERS + 7}")) == 2)
    check("counters are evicted alongside the buffers",
          len(m.exchange_counts) <= MAX_TRACKED_USERS,
          str(len(m.exchange_counts)))

    # Talking again should keep someone alive against eviction.
    m.add_exchange("still here", "Hm.", "chat",
                   f"viewer{MAX_TRACKED_USERS}")
    for i in range(5):
        m.add_exchange("new", "Hm.", "chat", f"latecomer{i}")
    check("an active viewer survives newcomers",
          len(m.get_history("chat", f"viewer{MAX_TRACKED_USERS}")) > 0)


def test_notes_persist_independently_of_buffers():
    print("\n--- notes outlive the buffer ---")
    m = fresh()
    m.add_exchange("hi", "Hm.", "chat", "alice")
    m.update_user_notes("alice", "Plays support, asks about the fox thing.")

    # Evict her by flooding.
    for i in range(MAX_TRACKED_USERS + 2):
        m.add_exchange("x", "Hm.", "chat", f"flood{i}")

    check("her thread is gone", m.get_history("chat", "alice") == [])
    check("but her notes remain",
          "asks about the fox thing" in m.get_user_notes("alice"),
          m.get_user_notes("alice"))
    m.user_notes.clear()


def main():
    test_threads_do_not_mix()
    test_notes_are_written_from_one_persons_words()
    test_note_trigger_is_per_person()
    test_compression_clears_only_that_person()
    test_stream_summary_still_spans_everybody()
    test_unidentified_speakers_share_one_buffer()
    test_tracked_users_are_bounded()
    test_notes_persist_independently_of_buffers()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
