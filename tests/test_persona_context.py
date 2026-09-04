"""
What reaches the model, and what must not.

    python tests/test_persona_context.py

Two questions this answers concretely.

**Do the game instructions enter her memory?** No, and that is deliberate. The
SITUATION block, the ANGLE, the TONE and the theme's opening are scaffolding for
one response. Storing them would replay stale instructions as if they were
things somebody said to her — which is exactly what made her repeat herself
until a session was terminated (see tests/test_game_memory.py).

**Is her appearance available?** It is now. `persona/ravyn.json` carried her
whole look and nothing loaded it, so "nice jacket" got an improvised answer that
could contradict the avatar. It is loaded with a hard rule against narrating it,
because giving her ear and tail vocabulary is what feeds the narration failure
`_strip_narration` already exists to clean up.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persona.context_builder import (                     # noqa: E402
    APPEARANCE_BLOCK, build_messages,
)
from persona.memory import MemoryManager                  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


GAME_CONTEXT = {
    "trigger": "game_event",
    "event_type": "MyDeath",
    "player_champion": "Riven",
    "situation": "SITUATION — measured from the game.\n  18 minutes in.",
    "player_notes": "WHAT EXILED HAS TOLD YOU\n  On Riven: \"I always lose\"",
    "angle": "Ask him one pointed question about what he thought would happen.",
    "tone_instruction": "TONE: Full roast.",
    "theme_opening": "He has queued jungle, his worst role.",
    "death_count": 3,
}


def test_scaffolding_reaches_the_prompt():
    print("\n--- the scaffolding reaches the prompt ---")
    messages = build_messages("You died.", "game", GAME_CONTEXT, history=[])
    user = messages[-1]["content"]

    check("the situation is in the prompt", "18 minutes in" in user)
    check("his notes are in the prompt", "I always lose" in user)
    check("the angle is in the prompt", "pointed question" in user)
    check("the tone is in the prompt", "Full roast" in user)
    check("history is empty for a game event", len(messages) == 2,
          str(len(messages)))


def test_scaffolding_never_enters_memory():
    print("\n--- and never enters memory ---")
    m = MemoryManager()
    m.histories.clear()
    m.recent_game_lines.clear()
    m.exchange_counts.clear()

    # This is what the worker does for a game event: only what she SAID.
    m.add_game_line("What was the plan there?")

    check("no history entry is created", m.get_history("game") == [])
    check("nor for chat", m.get_history("chat") == [])

    stored = " ".join(m.recent_game_lines)
    for label, fragment in [
        ("the situation block", "18 minutes in"),
        ("his notes", "I always lose"),
        ("the angle", "pointed question"),
        ("the tone", "Full roast"),
        ("the theme opening", "worst role"),
    ]:
        check(f"{label} is not stored", fragment not in stored)

    check("only her own line is kept",
          list(m.recent_game_lines) == ["What was the plan there?"],
          str(list(m.recent_game_lines)))

    # Chat DOES keep history — but the raw message, not the framed prompt.
    m.add_exchange(user_msg="hey ravyn", assistant_msg="Hm.", source="chat",
                   user="someviewer")
    kept = [e["content"] for e in m.get_history("chat", "someviewer")]
    check("chat history keeps the raw message", "hey ravyn" in kept, str(kept))
    check("and not a framed prompt",
          not any("SITUATION" in c or "YOUR ANGLE" in c for c in kept))


def test_appearance():
    print("\n--- her appearance ---")
    check("it is loaded at all", bool(APPEARANCE_BLOCK))
    for label, fragment in [
        ("the jacket", "orange jacket"),
        ("her ears", "Fox ears"),
        ("both eye colours", "Red"),
        ("the scar", "scar"),
        ("the choker", "choker"),
    ]:
        check(f"{label} is available to her", fragment in APPEARANCE_BLOCK)

    check("it is framed as answers, not description",
          "for answering questions" in APPEARANCE_BLOCK)
    check("and carries the hard rule against narrating it",
          "never narrate" in APPEARANCE_BLOCK.lower())

    # It is in EVERY prompt, including game events, so size matters at 4096.
    check("it stays small enough for a 4096 context",
          len(APPEARANCE_BLOCK) < 1200, f"{len(APPEARANCE_BLOCK)} chars")

    messages = build_messages("hi", "chat", {"user": "someviewer"}, history=[])
    check("it reaches the system message",
          "orange jacket" in messages[0]["content"])

    # Personality and speech rules stay in system_prompt.txt only — two sources
    # for one rule is how they drift apart.
    check("personality is not duplicated from ravyn.json",
          "ice side" not in APPEARANCE_BLOCK)
    check("speech rules are not duplicated either",
          "2-3 sentences" not in APPEARANCE_BLOCK)


def test_viewer_notes_reach_the_prompt():
    print("\n--- viewer notes ---")
    messages = build_messages(
        "hey", "chat", {"user": "someviewer"}, history=[],
        user_memory="Plays support, asks about the fox thing constantly.")
    system = messages[0]["content"]
    user = messages[-1]["content"]

    check("the note reaches the system message",
          "asks about the fox thing" in system, system[-200:])
    check("and is attributed to that viewer", "someviewer" in system.lower())
    check("the framed message mentions them too", "someviewer" in user.lower())

    bare = build_messages("hey", "chat", {"user": "newviewer"}, history=[])
    check("a viewer with no notes adds nothing",
          "WHAT YOU KNOW ABOUT" not in bare[0]["content"])


def test_chat_does_not_tunnel_into_the_game():
    """
    From a live session: asked whether she was clever, and asked about her
    clothes, she answered with his bad plays both times.

    Her persona carries a LEAGUE OF LEGENDS section and her history is full of
    game reactions, so left alone she pulls every subject back to the game. She
    is a fox spirit who happens to watch someone play, not a coach.
    """
    print("\n--- she answers what was asked ---")
    from persona.context_templates import CHAT_EXILED, CHAT_MESSAGE

    for name, template in (("Exiled", CHAT_EXILED), ("a viewer", CHAT_MESSAGE)):
        check(f"{name} is told to answer what was actually asked",
              "actually asked" in template, template[:60])
        check(f"{name} is told not to steer back to the game",
              "game" in template and ("do not steer" in template
                                      or "do not drag" in template))

    check("and she is allowed to talk about herself",
          "about yourself" in CHAT_EXILED)

    owner = build_messages("@Ravyn ты умная ?", "chat",
                           {"user": "exiledra1n", "is_owner": True},
                           history=[])[-1]["content"]
    check("it reaches his prompt", "actually asked" in owner)

    system = build_messages("hi", "chat", {"user": "someviewer"},
                            history=[])[0]["content"]
    check("the persona says the game is not all she is",
          "not the only thing you are" in system)


def main():
    test_scaffolding_reaches_the_prompt()
    test_scaffolding_never_enters_memory()
    test_appearance()
    test_viewer_notes_reach_the_prompt()
    test_chat_does_not_tunnel_into_the_game()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
