"""
Numbers she says about a game must be numbers she was given.

On 2026-11-06 she told him "you're sitting pretty with twenty-two kills" (22
was his TEAM's total), "eighteen CS per minute" (nothing in the game said 18
per minute), and "level 15 with twelve kills each". The PC hands her a
measured SITUATION block; a small model still blends, adds and invents
numbers around it. The prompt now labels every number, and this is the check
behind it: any number in her line that appears nowhere in what she was given
is invented, and gets one retry, then the sentence goes.

It cannot tell whose number is whose — "you have 22 kills" when 22 is the
team's passes. The labelling on the PC (orchestrator/game_state.py) is what
fixes attribution; this catches the numbers that came from nowhere.

Digits always count. Number WORDS are read in English only: Russian
declension makes a word list unreliable, and the Russian lines she speaks
mostly use digits anyway.
"""

from __future__ import annotations

import re

_UNITS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}

# "one" is left out on purpose: "one of them", "a one-man show", "at least
# one of you" are idiom, not a count. One and two as digits are still checked.
_WORD = re.compile(
    r"\b(?:(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(
        ["one"] + [k for k in _UNITS if 0 < _UNITS[k] < 10]) + r"))?|("
    + "|".join(_UNITS) + r"))\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")

# Always fine to say: a count of one or two is almost never a claim worth
# checking ("two kills for his trouble" came straight from the event, but
# "a couple", "the two of you" are everywhere), and a hundred is a figure of
# speech far more often than a stat.
ALWAYS_ALLOWED = {0, 1, 2, 100}


def numbers_in(text: str) -> list[tuple[int, str]]:
    """(value, as written) for every number in the text, digits and words."""
    found = [(int(m.group(0)), m.group(0)) for m in _DIGITS.finditer(text)]
    for m in _WORD.finditer(text):
        if m.group(1):
            value = _TENS[m.group(1).lower()]
            if m.group(2):
                # "one" is only in _UNITS' place here, as the tail of a
                # compound — "twenty-one" is 21, never 20.
                value += _UNITS.get(m.group(2).lower(), 1)
        else:
            value = _UNITS[m.group(3).lower()]
        found.append((value, m.group(0)))
    return found


def unsupported(line: str, given: str) -> list[str]:
    """The numbers in her line that appear nowhere in what she was given."""
    allowed = {v for v, _ in numbers_in(given)} | ALWAYS_ALLOWED
    return [written for value, written in numbers_in(line)
            if value not in allowed]


_SENTENCE = re.compile(r"[^.!?…]+(?:[.!?…]+|$)")


def without_sentences_naming(line: str, given: str) -> str:
    """Her line with every sentence that carries an invented number removed."""
    kept = [s for s in _SENTENCE.findall(line)
            if s.strip() and not unsupported(s, given)]
    return " ".join(s.strip() for s in kept).strip()
