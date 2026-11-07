"""
Numbers she says about a game must be numbers she was given.

    python tests/test_numbers.py

The lines are the real ones from 2026-11-06.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persona import numbers                                # noqa: E402

FAILURES: list[str] = []

GIVEN = """GAME EVENT: double kill! Even I'm impressed. Almost.
SITUATION — measured from the game.
  34 minutes in (late game).
  Exiled (Katarina, mid): level 16. HIS OWN score: 12 kills, 6 deaths, 4 assists. His CS: 210 (6.2 per minute; third most CS of all ten players).
  Team kill totals (all five players added up — NOT his own): your team 31, theirs 37.
  His teammates, each kills/deaths/assists: Yone 5/7/3, Vel'Koz 4/6/9."""


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main():
    print("\n--- invented numbers are caught ---")
    said = ("The rest of us are barely scraping by at level 15 with twelve "
            "kills each, yet here you are at eighteen CS per minute.")
    bad = numbers.unsupported(said, GIVEN)
    check("eighteen CS per minute is invented", "eighteen" in bad, str(bad))
    check("so is level 15", "15" in bad, str(bad))
    check("his real twelve kills are fine", "twelve" not in bad, str(bad))

    check("numbers that are there pass",
          numbers.unsupported("Yone with five kills and Vel'Koz with four, "
                              "you at twelve, thirty-four minutes in.",
                              GIVEN) == [])
    check("compound words are read whole",
          numbers.numbers_in("thirty-seven") == [(37, "thirty-seven")])
    check("'one of them' is idiom, not a count",
          numbers.unsupported("One of them finally did something.", GIVEN) == [])
    check("words inside words are not numbers",
          numbers.unsupported("Often a tenet of patience.", GIVEN) == [])

    print("\n--- and cut when the retry invents again ---")
    line = "Nice double. You hit eighteen CS per minute. Keep it up."
    check("only the sentence with the invented number goes",
          numbers.without_sentences_naming(line, GIVEN)
          == "Nice double. Keep it up.",
          numbers.without_sentences_naming(line, GIVEN))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
