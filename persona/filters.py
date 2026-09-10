"""
Keeping her in character — and counting when she is not.

These used to live inline in `adapters/mq/rabbitmq.py`, where they could not be
tested and their patterns could not be inspected without reading the worker.
They are the last line between the model and the TTS, and everything that gets
through gets SPOKEN, so they earn a file.

The tally is the point as much as the filters are. "She feels off today" is not
something you can act on; `[filters] narration 3, leak 1, over-length 7` is.
Every intervention is counted per session and printed on a schedule, so
character drift becomes a number that moves when a prompt changes.

Two rules govern what belongs here:

  * Filter what is UNAMBIGUOUS. She talks about teammates in the third person
    constantly, so a bare "she/he + verb" rule would eat real speech. Her own
    name plus a verb cannot be anything but narration.
  * Never silently improve her. Anything removed is counted, and a response
    that was ENTIRELY narration returns empty rather than half a sentence —
    saying nothing beats saying scaffolding out loud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Her prompt caps her at 2-3 sentences. Beyond that is the model ignoring
# instructions, and the PC would silently truncate it mid-thought anyway — so
# it is cut here, where it can be counted.
MAX_SENTENCES = 3

# --- narration ----------------------------------------------------------
#
# Matched BY NAME, never by pronoun. She refers to teammates as "she/he"
# constantly, so a pronoun rule would eat real speech; her own name plus a verb
# cannot be anything but prose about her.
#
# Both tenses and the possessive, because the original only caught the present:
# "Ravyn tilts" was removed while "Ravyn tilted her head" and "Ravyn's ears
# flick" both survived and were read aloud.
_NARRATION_SELF = re.compile(
    r"\bRavyn(?:'s|’s)?\s+\w+(?:s|ed|ing)\b", re.IGNORECASE)

# Dialogue attribution after a closing quote: '"NewViewer_123," she murmurs to
# no one.' The quote-comma-pronoun-verb shape only occurs in prose, so this is
# safe where a bare pronoun rule would not be. The comma usually sits INSIDE
# the closing quote, so it has to be consumed or it survives as a dangling
# 'NewViewer_123,.'
_DIALOGUE_TAG = re.compile(
    r',?\s*(["“”])\s*,?\s*(?:she|he|they)\s+\w+s\b[^.!?]*([.!?])',
    re.IGNORECASE)

# --- stage directions ---------------------------------------------------
#
# Everything she writes is spoken aloud, so "*sighs*" is read as the word
# "sighs". The old length caps (20 chars in brackets, 30 in asterisks) let
# anything longer through — "*She leans back in her chair, unimpressed*" is
# forty and was being read out in full.
_ASTERISKS = re.compile(r"\*[^*]+\*")
_BRACKETED = re.compile(r"[\[(][^\])]{1,60}[\])]")

# Mood tags are structural and parsed elsewhere; they must survive this.
_MOOD_TAG = re.compile(r"\[(?:mood|tired):[^\]]*\]", re.IGNORECASE)

# --- instruction leak ---------------------------------------------------
#
# She is handed an angle, a tone and a situation block, and sometimes she
# describes them instead of performing them. Live: "some topics are said
# directly and not accepted as theme or tone of conversation".
#
# Deliberately narrow. These are phrases that only appear when she is talking
# ABOUT her own instructions — a broad rule here would delete real lines, which
# is far worse than letting one slip through.
# Bare "the angle" is NOT here, and that is deliberate: "you have the angle on
# him" is a real thing she says about a fight. Possessives are the tell — she
# only says "my angle" when she means the instruction she was handed.
_LEAK = re.compile(
    r"\b("
    r"my angle(?:\s+(?:here|this time|is))?"
    r"|your angle this time"
    r"|my tone(?:\s+(?:here|is))?"
    r"|the tone (?:is|should)"
    r"|the situation (?:block|says|above)"
    r"|the instruction"
    r"|as instructed"
    r"|(?:the system|my) prompt"
    r"|the seed(?:\s+text)?"
    r"|i (?:should|must|am supposed to) (?:be|sound|react|respond|say)"
    r"|the event (?:says|above)"
    r"|according to the (?:situation|event|instruction)"
    r")\b", re.IGNORECASE)

# Block labels she echoes verbatim. These end in a colon, so they cannot carry
# a trailing \b — a word boundary after ":" needs a word character next, and
# what follows is a space. The original had "game event:" inside the bounded
# group above and therefore never matched anything at all.
_LEAK_LABEL = re.compile(
    r"^\s*(?:GAME EVENT|SITUATION|TONE|YOUR ANGLE[^:]*|WHAT YOU CALL[^:]*"
    r"|WHAT EXILED[^:]*)\s*:", re.IGNORECASE)

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]+")

_QUOTE_CHARS = '"“”'


@dataclass
class Tally:
    """
    How often she needed cleaning up, this session.

    A count that moves when a prompt changes is the difference between tuning
    her and guessing at her.
    """
    responses: int = 0
    narration: int = 0
    all_narration: int = 0      # nothing survived; she said nothing
    leak: int = 0
    stage_directions: int = 0
    emoji: int = 0
    over_length: int = 0

    def summary(self) -> str:
        if not self.responses:
            return "no responses yet"
        parts = []
        for label, value in (("narration", self.narration),
                             ("silenced", self.all_narration),
                             ("leak", self.leak),
                             ("stage-dir", self.stage_directions),
                             ("emoji", self.emoji),
                             ("over-length", self.over_length)):
            if value:
                parts.append(f"{label} {value}")
        clean = "clean" if not parts else ", ".join(parts)
        return f"{self.responses} responses: {clean}"


@dataclass
class Result:
    text: str
    removed: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.removed)


def clean(text: str, tally: Tally | None = None) -> Result:
    """
    Everything between the model and the TTS, in one pass.

    Order matters: stage directions and emoji go first so that what remains is
    the sentences she actually meant to say, and only those are counted against
    the narration and length rules.
    """
    removed: list[str] = []
    if tally is not None:
        tally.responses += 1

    if not text:
        return Result("", removed)

    # Mood tags are parsed by the caller, so hold them aside rather than let
    # the bracket rule eat them.
    tags = _MOOD_TAG.findall(text)
    text = _MOOD_TAG.sub(" ", text)

    text, hit = _strip(text, _ASTERISKS, _BRACKETED)
    if hit:
        removed.append("stage_directions")
        if tally:
            tally.stage_directions += 1

    if _EMOJI.search(text):
        text = _EMOJI.sub("", text)
        removed.append("emoji")
        if tally:
            tally.emoji += 1

    if _LEAK.search(text) or _LEAK_LABEL.search(text):
        kept = [s for s in _sentences(text)
                if not _LEAK.search(s) and not _LEAK_LABEL.search(s)]
        text = " ".join(kept)
        removed.append("leak")
        if tally:
            tally.leak += 1

    before = _norm(text)
    text = _DIALOGUE_TAG.sub(r"\1\2", text)
    text = " ".join(s for s in _sentences(text)
                    if not _NARRATION_SELF.search(s))
    if _norm(text) != before:
        removed.append("narration")
        if tally:
            tally.narration += 1

    # She never puts quotation marks around her own words.
    text = text.strip().strip(_QUOTE_CHARS).strip()

    sentences = _sentences(text)
    if len(sentences) > MAX_SENTENCES:
        text = " ".join(sentences[:MAX_SENTENCES])
        removed.append("over_length")
        if tally:
            tally.over_length += 1

    text = re.sub(r"\s+", " ", text).strip()

    if not text and tally:
        tally.all_narration += 1

    return Result(text + ("" if not tags else " " + " ".join(tags)), removed)


def _strip(text: str, *patterns) -> tuple[str, bool]:
    hit = False
    for pattern in patterns:
        if pattern.search(text):
            hit = True
            text = pattern.sub(" ", text)
    return text, hit


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip(_QUOTE_CHARS)).strip()
