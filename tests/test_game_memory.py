"""
Game events must not be treated as conversation.

    python tests/test_game_memory.py

From a live session, terminated by the streamer: "she was saying Liss was doing
something while I farm measly CS, she said it 5-6 times in a row".

MAX_HISTORY is 5, and that is not a coincidence. Every game event went through
`add_exchange` like a chat message, so the next one arrived at the LLM with her
last five game reactions replayed as a conversation — each "user turn" being
the entire framed prompt, SITUATION block and all. She was shown five
near-identical setups and her own five answers to them, then asked for a sixth.
She said the same thing again, which is what a five-deep history of near-
identical turns predicts.

Opener-based anti-repetition could not save it: she varied the first four words
and repeated the substance. So game events now carry no history, and what she
actually SAID is kept separately for the sole purpose of telling her not to say
it again.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persona.memory import MemoryManager, MAX_GAME_LINES   # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def fresh() -> MemoryManager:
    m = MemoryManager()
    m.histories.clear()
    m.recent_openers.clear()
    m.recent_game_lines.clear()
    m.exchange_counts.clear()
    return m


def test_history_is_withheld_from_game_events():
    print("\n--- game events carry no conversation history ---")
    m = fresh()
    for i in range(5):
        m.add_exchange(user_msg=f"viewer said {i}",
                       assistant_msg=f"she replied {i}", source="chat")

    check("chat still gets its history", len(m.get_history("chat")) == 10,
          str(len(m.get_history("chat"))))
    check("game events get none", m.get_history("game") == [],
          str(m.get_history("game")))
    check("an unspecified source still reads the buffer",
          len(m.get_history()) == 10)


def test_game_lines_are_kept_apart():
    print("\n--- what she said is kept, as an anti-repetition list ---")
    m = fresh()
    m.add_game_line("Your jungler is farming while the map burns.")
    m.add_game_line("Measly CS for all that farming.")

    check("game lines do not enter conversation history", m.get_history() == [])
    check("nor the per-person counts that drive note writing",
          m.exchange_counts == {}, str(m.exchange_counts))
    check("nor the chat opener list", len(m.recent_openers) == 0)

    guard = m.get_repetition_guard("game")
    check("both lines reach the guard",
          "map burns" in guard and "Measly CS" in guard, guard)
    check("the guard forbids the substance, not just the opening",
          "do not rephrase them" in guard, guard)
    check("and tells her what to do instead",
          "find a different detail" in guard, guard)

    check("chat still gets opener-based guidance instead",
          m.get_repetition_guard("chat") == m.get_recent_openers())

    check("an empty game guard is empty", fresh().get_repetition_guard("game") == "")


def test_game_lines_are_bounded_and_reset():
    print("\n--- bounded, and reset between games ---")
    m = fresh()
    for i in range(MAX_GAME_LINES + 6):
        m.add_game_line(f"line {i}")
    check(f"at most {MAX_GAME_LINES} lines are kept",
          len(m.recent_game_lines) == MAX_GAME_LINES,
          str(len(m.recent_game_lines)))
    check("and they are the most recent ones",
          "line 0" not in m.get_repetition_guard("game")
          and f"line {MAX_GAME_LINES + 5}" in m.get_repetition_guard("game"))

    m.clear_game_lines()
    check("a new game starts from nothing", m.get_repetition_guard("game") == "")

    m.add_game_line("   ")
    check("blank lines are not stored", len(m.recent_game_lines) == 0)


def main():
    test_history_is_withheld_from_game_events()
    test_game_lines_are_kept_apart()
    test_game_lines_are_bounded_and_reset()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
