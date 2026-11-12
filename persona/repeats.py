"""
Has she already said this tonight?

The prompt-side guard (persona/memory.py, "YOU HAVE ALREADY SAID THESE") asks a
9B model not to repeat its last six game lines. It does not hold: the PC's
2026-10-25 session measured 18.8% of her lines echoing earlier ones, and every
phrase that was patched out was replaced by a new one. Asking is not enforcing.

So this checks every line against everything she has said this session, after
generation and before it is spoken. It is the same measure the PC's session
analysis uses (ravyn-lynx-p/orchestrator/session_metrics.py), so "a repeat"
means one thing on both sides:

    a shared run of 5 words with at least three real words of her own, or
    line similarity >= 0.50 (order-aware, bag-of-words, or same opening)

Tuned on the two real 2026-10-25 sessions: a single shared 4-word run flagged
64% of lines, mostly ordinary English ("while the rest of"). Five words with
three of her own flag the lines a listener actually hears twice — "own team
spinning circles", "barely early enough to care" — at 20-30%.

What she was GIVEN does not count — his champion notes, the teammate words,
the trigger, the game situation. Saying back what he told her is the point of
those; it is her own phrasing coming back that wears thin.
"""

from __future__ import annotations

import re
from collections import deque
from difflib import SequenceMatcher

SIMILARITY = 0.50
PHRASE_WORDS = 5
PHRASE_OWN_WORDS = 3     # words in the run that are neither filler nor given
OPENING_WORDS = 5
OPENING_SCORE = 0.75
MAX_LINES = 300          # a long stream; the oldest fall off

# Shorter than a 5-word run, and what a listener actually hears. On
# 2026-11-11 every spoken line passed the rules above while "watching paint
# dry" was said four times in one game and "honestly, I'm just glad" seven
# times in the next.
#   A catchphrase: three words in a row, none of them filler or given. Once
#   is hers; the second time is a tic.
#   A habit: a 4-word run with two words of her own, already in
#   HABIT_USES of her last HABIT_WINDOW lines. Two uses are how people
#   talk; the third is the one that grates.
CATCH_WORDS = 3
HABIT_WORDS = 4
HABIT_OWN_WORDS = 2
HABIT_USES = 2
HABIT_WINDOW = 40

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "is", "are", "was", "were", "be", "been", "it", "its",
    "that", "this", "these", "those", "as", "so", "not", "no", "just",
}

# Too common to make a phrase on their own: "you know what i" is how people
# talk, not a catchphrase. A run needs two words outside this to count.
FILLER = STOPWORDS | {
    "i", "you", "me", "my", "your", "he", "she", "they", "we", "him", "her",
    "them", "his", "their", "our", "what", "know", "think", "really", "do",
    "did", "does", "have", "has", "had", "like", "get", "got", "can", "will",
    "would", "could", "there", "here", "then", "now", "all", "more", "one",
    "i'm", "you're", "it's", "that's", "don't", "about", "how", "who", "why",
    "when", "where", "up", "out", "too", "very", "well", "oh", "hmm", "hah",
}

_WORD = re.compile(r"[^\w']+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w for w in _WORD.sub(" ", (text or "").lower()).split() if w]


def _grams(toks: list[str], given: set[str] = frozenset()) -> set[tuple]:
    out = set()
    for i in range(len(toks) - PHRASE_WORDS + 1):
        gram = tuple(toks[i:i + PHRASE_WORDS])
        own = sum(1 for w in gram if w not in FILLER and w not in given)
        if own >= PHRASE_OWN_WORDS:
            out.add(gram)
    return out


def _runs(toks: list[str], size: int, own_needed: int,
          given: set[str] = frozenset()) -> set[tuple]:
    out = set()
    for i in range(len(toks) - size + 1):
        gram = tuple(toks[i:i + size])
        if sum(1 for w in gram
               if w not in FILLER and w not in given) >= own_needed:
            out.add(gram)
    return out


def similarity(a: list[str], b: list[str]) -> float:
    """Same measure as session_metrics.similarity on the PC."""
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ca = [w for w in a if w not in STOPWORDS]
    cb = [w for w in b if w not in STOPWORDS]
    sa, sb = set(ca), set(cb)
    jaccard = len(sa & sb) / len(sa | sb) if sa and sb else 0.0
    opening = (OPENING_SCORE
               if (len(ca) >= OPENING_WORDS and len(cb) >= OPENING_WORDS
                   and ca[:OPENING_WORDS] == cb[:OPENING_WORDS]) else 0.0)
    return max(seq, jaccard, opening)


class RepeatGuard:
    def __init__(self, max_lines: int = MAX_LINES):
        self._lines: deque = deque(maxlen=max_lines)

    def echo(self, text: str, given: str = "") -> str:
        """
        What this line repeats, as a short description for the log and the
        retry prompt — or "" if it is fresh.
        """
        toks = words(text)
        if not toks:
            return ""
        given_words = set(words(given))
        mine = _grams(toks, given_words)

        for old_toks, old_text in reversed(self._lines):
            shared = mine & _grams(old_toks)
            if shared:
                gram = max(shared, key=lambda g: sum(w not in FILLER for w in g))
                return f'the phrase "{" ".join(gram)}"'

            # Whole-line similarity, on her own words only: two lines that
            # both quote his notes are alike because of the notes.
            a = [w for w in toks if w not in given_words] or toks
            b = [w for w in old_toks if w not in given_words] or old_toks
            if similarity(a, b) >= SIMILARITY:
                return f'your earlier line "{old_text[:90]}"'

        recent = list(self._lines)[-HABIT_WINDOW:]

        catch = _runs(toks, CATCH_WORDS, CATCH_WORDS, given_words)
        for old_toks, _ in reversed(recent):
            shared = catch & _runs(old_toks, CATCH_WORDS, CATCH_WORDS)
            if shared:
                return f'the phrase "{" ".join(sorted(shared)[0])}"'

        habit = _runs(toks, HABIT_WORDS, HABIT_OWN_WORDS, given_words)
        if habit:
            uses: dict = {}
            for old_toks, _ in recent:
                for gram in habit & _runs(old_toks, HABIT_WORDS,
                                          HABIT_OWN_WORDS):
                    uses[gram] = uses.get(gram, 0) + 1
            worn = sorted(g for g, n in uses.items() if n >= HABIT_USES)
            if worn:
                return f'the phrase "{" ".join(worn[0])}"'
        return ""

    def add(self, text: str) -> None:
        toks = words(text)
        if toks:
            self._lines.append((toks, text.strip()))

    def __len__(self) -> int:
        return len(self._lines)


# ---------------------------------------------------------------------
# copying herself to one person
# ---------------------------------------------------------------------

# Shorter than this and a sentence is a stock phrase ("Hah.", "Fine."), not a
# copy worth cutting.
MIN_COPIED_WORDS = 5

_SENTENCE = re.compile(r"[^.!?…]+(?:[.!?…]+|$)")


def _norm(text: str) -> str:
    return " ".join(words(text))


def without_copies(line: str, earlier: list[str]) -> str:
    """
    Her line minus every sentence she already said, word for word, to this
    same person.

    Chat history is replayed per person, and on 2026-11-09 the model pasted
    its two previous replies to a viewer back as its answer to his third
    message; the length filter kept the first, so he heard the exact line
    she had said a minute before. The tonight-wide RepeatGuard does not run
    on chat (a viewer who asks twice is owed an answer twice), so this checks
    only her own earlier replies in this person's thread.
    """
    said = [_norm(e) for e in earlier if e]
    sentences = [x for x in _SENTENCE.findall(line or "") if x.strip()]
    copied = [bool(_norm(x)) and any(_norm(x) in s for s in said)
              for x in sentences]
    # One long copied sentence makes the line a paste; then the short
    # fragments pasted with it ("determined811...", "After work?") go too.
    # Without one, a stock "Hah." that happens to recur is left alone.
    pasted = any(c and len(_norm(x).split()) >= MIN_COPIED_WORDS
                 for x, c in zip(sentences, copied))
    if not pasted:
        return (line or "").strip()
    return " ".join(x.strip() for x, c in zip(sentences, copied)
                    if not c).strip()

