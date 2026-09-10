"""
Staying in character — and counting when she does not.

    python tests/test_filters.py

Everything she writes is SPOKEN, so anything these miss is read aloud: "*She
leans back, unimpressed*" becomes the words "she leans back unimpressed", and
"My angle here is to be dismissive" becomes her narrating her own instructions
at the viewer.

Two rules the tests hold:

  * Filter only what is UNAMBIGUOUS. She talks about teammates in the third
    person constantly, so a bare "she/he + verb" rule would eat real speech.
    Her own name plus a verb cannot be anything but prose about her.
  * Never silently improve her. Every removal is counted, and a response that
    was entirely scaffolding returns empty rather than half a sentence.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persona.filters import MAX_SENTENCES, Tally, clean   # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def test_narration_by_name():
    print("\n--- prose about her, in every tense ---")
    # The original pattern was \bRavyn\s+\w+s\b — present tense only. The past
    # tense and the possessive were read out in full.
    for prose in ("Ravyn tilts her head at the chat notification.",
                  "Ravyn tilted her head, unimpressed.",
                  "Ravyn is watching the minimap closely.",
                  "Ravyn's ears flick once.",
                  "Ravyn leans back, saying nothing."):
        check(f"{prose[:38]!r} is removed", clean(prose).text == "",
              clean(prose).text)

    kept = clean("Hah. That was rough.")
    check("real speech is untouched", kept.text == "Hah. That was rough.")
    check("and is not counted as a removal", not kept.changed)

    # She calls teammates "she"/"he" constantly. A pronoun rule would eat this.
    for real in ("She is one and nine. Rough.",
                 "He walked into all five of them.",
                 "They keep dying on the same wall."):
        check(f"{real[:34]!r} survives", clean(real).text == real)


def test_dialogue_attribution():
    print("\n--- dialogue tags ---")
    out = clean('"NewViewer_123," she murmurs to no one and everyone.').text
    check("the attribution goes", "murmurs" not in out, out)
    check("and nothing dangles", "," not in out.strip(",. "), out)


def test_stage_directions():
    print("\n--- stage directions are spoken aloud ---")
    # The old caps were 20 chars in brackets and 30 in asterisks, so anything
    # longer was read out.
    long_one = "*She leans back in her chair, completely unimpressed* Whatever."
    check("a long asterisk direction goes",
          clean(long_one).text == "Whatever.", clean(long_one).text)

    check("a bracketed one goes",
          clean("[sighs heavily and looks away] Fine.").text == "Fine.")
    check("a parenthesised one goes",
          clean("(rolls her eyes) Sure.").text == "Sure.")
    check("short ones still go", clean("*sighs* Fine.").text == "Fine.")


def test_instruction_leak():
    print("\n--- she narrates her own instructions ---")
    for leaked in ("My angle here is to be dismissive. Fine, whatever.",
                   "The tone is roast, so: you walked into that.",
                   "I should be dismissive about this. Sure.",
                   "According to the situation, you are eight kills down.",
                   "GAME EVENT: your teammate died. Typical."):
        out = clean(leaked).text
        check(f"{leaked[:34]!r} loses the scaffolding",
              "angle" not in out.lower() and "GAME EVENT" not in out,
              out)

    # Narrow on purpose — deleting a real line is far worse than missing one.
    # The narrow-on-purpose half. Each of these was eaten by a first pass at
    # the patterns, and each is something she would plausibly say.
    for real in ("You have the angle on him, take it.",
                 "The angle was there and you walked past it.",
                 "Your tone changed the second you died.",
                 "That was the prompt he needed, apparently.",
                 "I should have known."):
        check(f"{real[:34]!r} is not touched", clean(real).text == real,
              clean(real).text)


def test_emoji_and_quotes():
    print("\n--- emoji and self-quoting ---")
    check("emoji go", clean("That was rough. 😂").text == "That was rough.")
    check("she does not quote herself",
          clean('"Fine, whatever."').text == "Fine, whatever.")
    check("smart quotes too", clean("“Fine.”").text == "Fine.")


def test_length_cap():
    print("\n--- the two-to-three sentence rule ---")
    long_answer = "One. Two. Three. Four. Five."
    out = clean(long_answer)
    check(f"cut to {MAX_SENTENCES} sentences",
          out.text == "One. Two. Three.", out.text)
    check("and counted", "over_length" in out.removed, str(out.removed))
    check("three is fine", not clean("One. Two. Three.").changed)

    # The PC truncates to three anyway; doing it here means it is COUNTED
    # rather than silently losing the tail mid-thought.
    check("an in-range answer is untouched",
          clean("Hah. Rough.").text == "Hah. Rough.")


def test_mood_tags_survive():
    print("\n--- mood tags are structure, not speech ---")
    out = clean("Hah. That is rough. [mood:0.3] [tired:0.1]").text
    check("the mood tag survives the bracket rule", "[mood:0.3]" in out, out)
    check("so does tired", "[tired:0.1]" in out, out)
    check("and the speech is intact", "Hah. That is rough." in out, out)


def test_tally_is_the_measurement():
    print("\n--- the tally ---")
    t = Tally()
    check("nothing yet reads as nothing yet", "no responses" in t.summary())

    clean("Hah. Rough.", t)
    clean("Hah. Rough.", t)
    check("clean responses are counted", t.responses == 2, str(t.responses))
    check("and reported as clean", "clean" in t.summary(), t.summary())

    clean("Ravyn tilts her head.", t)
    check("narration is counted", t.narration == 1)
    check("and a fully silenced response is counted separately",
          t.all_narration == 1)

    clean("My angle is to be bored. Whatever.", t)
    clean("One. Two. Three. Four.", t)
    clean("Nice. 😂", t)
    check("leak is counted", t.leak == 1)
    check("over-length is counted", t.over_length == 1)
    check("emoji is counted", t.emoji == 1)

    summary = t.summary()
    check("the summary names what happened",
          all(w in summary for w in ("narration", "leak", "over-length")),
          summary)
    check("and how many responses it is out of",
          summary.startswith("6 responses"), summary)


def test_never_raises():
    print("\n--- it cannot take the worker down ---")
    for odd in ("", "   ", ".", "!!!", "\n\n", "*", "[", '"',
                "Ravyn", "😂", "[mood:0.1]"):
        try:
            clean(odd, Tally())
            ok = True
        except Exception as e:
            ok = False
            print(f"      {odd!r} raised {type(e).__name__}: {e}")
        check(f"{odd!r} is survivable", ok)


def main():
    test_narration_by_name()
    test_dialogue_attribution()
    test_stage_directions()
    test_instruction_leak()
    test_emoji_and_quotes()
    test_length_cap()
    test_mood_tags_survive()
    test_tally_is_the_measurement()
    test_never_raises()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
