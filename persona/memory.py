"""
Memory manager — conversation history, compression, and per-user notes.

Short-term: last 5 exchanges **per person**, in their own buffer.
Long-term: LLM-compressed summary of the stream + per-user notes.
Persists to disk as JSON, survives restarts.

History used to be one shared buffer, which was fine while the streamer was
the only chatter and wrong the moment he was not. Two things broke:

  * A reply to one viewer carried the last five messages from *everyone* as if
    they were one conversation, so she answered person C in the middle of
    person A's thread.
  * `get_user_note_compression_prompt` built from that shared buffer and wrote
    the result to whoever happened to be active at the trigger. Five viewers
    talking meant the fifth one's notes were written from a transcript of all
    five — she would remember other people's personalities as theirs.

Now every person has their own buffer and their own exchange counter, so notes
are written from that person's messages only. What she can still see of the
room is `recent_chat`, which the PC already batches into context and which is
explicitly framed as other people talking, not as her conversation.
"""

from __future__ import annotations

import json
import time
from collections import deque

from app.settings import get_settings


settings = get_settings()
MEMORY_FILE = settings.DATA_DIR / "memory.json"
MAX_HISTORY = 5
MAX_USER_NOTE_LEN = 200
MAX_OPENING_TRACK = 5   # track last N response openers for anti-repetition

# Game reactions are kept apart from conversation history and in full.
#
# They used to go through add_exchange like chat, so every game event arrived
# at the LLM with her last five game reactions replayed as a conversation —
# each "user turn" being the whole framed prompt, situation block and all. She
# was being shown five near-identical setups and her own five answers to them,
# and asked for a sixth. She did the obvious thing and said the same thing
# again, which is exactly what a five-deep history predicts. Openers-only
# anti-repetition could not help: she varied the first four words and repeated
# the substance.
#
# So game events carry no history, and this holds what she actually SAID for
# the sole purpose of telling her not to say it again.
MAX_GAME_LINES = 6

# How many people to keep history for at once. A busy stream would otherwise
# grow a buffer per name forever; the least recently active is dropped, which
# costs that person their short-term thread and nothing else — their notes are
# persisted separately and survive.
MAX_TRACKED_USERS = 24

# The key used when nobody is identified. Voice from the streamer, or a chat
# source that failed to attribute a message, share this one buffer rather than
# silently joining someone else's.
ANON = ""


class MemoryManager:

    def __init__(self):
        # user key -> that person's rolling exchange buffer. Ordered, so the
        # first key is the least recently active and is what gets evicted.
        self.histories: dict[str, deque] = {}
        # user key -> exchanges since their notes were last written
        self.exchange_counts: dict[str, int] = {}
        self.general_memory = ""
        self.user_notes: dict[str, str] = {}

        # Who has paid for something, and how often. Kept apart from the prose
        # notes on purpose: those are written by the model and rewritten every
        # few exchanges, and a fact she should never get wrong — that this
        # person has donated three times — does not belong anywhere a model
        # can edit it.
        self.donors: dict[str, dict] = {}
        self.recent_openers: deque[str] = deque(maxlen=MAX_OPENING_TRACK)
        # Deliberately not persisted: it is scoped to the game being played,
        # and a restart is a new game.
        self.recent_game_lines: deque[str] = deque(maxlen=MAX_GAME_LINES)
        self._load()

    # ---------------------------------------------------------
    # history management
    # ---------------------------------------------------------

    @staticmethod
    def _key(user: str) -> str:
        return (user or ANON).strip().lower()

    def _buffer(self, user: str) -> deque:
        """That person's buffer, creating it and evicting the stalest if new."""
        key = self._key(user)

        if key in self.histories:
            # Touch it so it counts as most recently active.
            self.histories[key] = self.histories.pop(key)
        else:
            if len(self.histories) >= MAX_TRACKED_USERS:
                stalest = next(iter(self.histories))
                self.histories.pop(stalest, None)
                self.exchange_counts.pop(stalest, None)
            self.histories[key] = deque(maxlen=MAX_HISTORY * 2)

        return self.histories[key]

    def add_exchange(self, user_msg: str, assistant_msg: str, source: str = "", user: str = ""):
        """Add one exchange to that person's own buffer."""
        key = self._key(user)
        buffer = self._buffer(user)

        buffer.append({"role": "user", "content": user_msg})
        buffer.append({"role": "assistant", "content": assistant_msg})
        self.exchange_counts[key] = self.exchange_counts.get(key, 0) + 1

        # Openers are global on purpose: she should not start two consecutive
        # responses the same way regardless of who she is answering.
        words = assistant_msg.strip().split()[:4]
        if words:
            self.recent_openers.append(" ".join(words))

    def add_game_line(self, line: str) -> None:
        """Remember what she said about the game, for anti-repetition only."""
        line = (line or "").strip()
        if line:
            self.recent_game_lines.append(line)

    def clear_game_lines(self) -> None:
        self.recent_game_lines.clear()

    def get_history(self, source: str = "", user: str = "") -> list[dict]:
        """
        Conversation history for the messages array — that person's only.

        Empty for game events. A game reaction is not a turn in a conversation
        — nobody said anything to her — and replaying previous ones as if
        somebody had is what made her repeat herself. Her continuity across a
        game comes from the SITUATION block instead, which is both accurate and
        current, unlike a transcript of five stale prompts.

        For chat, a shared buffer meant answering one viewer while carrying
        another's thread. What she can see of the room is `recent_chat`, which
        is framed as other people talking rather than as her conversation.
        """
        if source == "game":
            return []
        return list(self.histories.get(self._key(user), ()))

    def get_repetition_guard(self, source: str = "") -> str:
        """
        What to tell her not to repeat.

        Chat gets openers — the substance should follow the conversation, only
        the phrasing needs to vary. Game events get the full lines, because
        there the substance is what repeats.
        """
        if source == "game":
            if not self.recent_game_lines:
                return ""
            lines = "\n".join(f'- "{line}"' for line in self.recent_game_lines)
            return ("YOU HAVE ALREADY SAID THESE, recently, about this game:\n"
                    f"{lines}\n"
                    "Do not repeat any of them, and do not rephrase them. If "
                    "the only thing you have to say is something on that list, "
                    "find a different detail in the situation to talk about.")
        return self.get_recent_openers()

    def get_recent_openers(self) -> str:
        """Return formatted recent openers for anti-repetition injection."""
        if not self.recent_openers:
            return ""
        lines = "\n".join(f'- "{o}"' for o in self.recent_openers)
        return f"Your recent response openings were:\n{lines}\nStart your next response with a DIFFERENT opening. Vary your first words."

    def needs_compression(self, user: str = "") -> bool:
        """True when THIS person has talked enough to be worth writing down."""
        return self.exchange_counts.get(self._key(user), 0) >= MAX_HISTORY

    def get_compression_payload(self) -> str:
        """
        Payload for the stream-wide summary.

        This one deliberately spans everybody: `general_memory` is "what has
        been happening on this stream", which is a property of the room rather
        than of one person. The per-user note below is the opposite.
        """
        lines = []
        for key, buffer in self.histories.items():
            who = key or "someone"
            for msg in buffer:
                role = "Ravyn" if msg["role"] == "assistant" else who
                lines.append(f"{role}: {msg['content']}")

        conversation = "\n".join(lines)

        prompt = f"""Summarize this conversation in 2-3 short sentences. Focus on: who talked, what topics came up, what mood Ravyn was in, and any notable moments. Be concise.

Previous context: {self.general_memory or 'None yet.'}

Recent conversation:
{conversation}

Write ONLY the summary, nothing else."""

        return prompt

    def apply_compression(self, summary: str, active_user: str = ""):
        """
        Store the summary and clear only the person it was written for.

        Clearing everyone would throw away threads with viewers who had not
        yet reached the trigger — they would lose their context because
        somebody else happened to hit five exchanges first.
        """
        self.general_memory = summary.strip()

        key = self._key(active_user)
        if key in self.histories:
            self.histories[key].clear()
        self.exchange_counts[key] = 0
        self._save()

        print(f"[memory] Compressed: {self.general_memory[:80]}...")

    # ---------------------------------------------------------
    # per-user notes
    # ---------------------------------------------------------

    def get_user_notes(self, user: str) -> str:
        """Get notes about a specific user."""
        if not user:
            return ""
        return self.user_notes.get(user.lower(), "")

    def update_user_notes(self, user: str, notes: str):
        """Update notes for a user. Truncates if too long."""
        if not user:
            return
        key = user.lower()
        truncated = notes[:MAX_USER_NOTE_LEN]
        self.user_notes[key] = truncated
        self._save()

    def note_donation(self, user: str, amount: float,
                      currency: str = "$") -> None:
        """
        A donation leaves a small, permanent note on whoever sent it.

        Counted rather than appended: a line per donation would grow past the
        note cap and push out everything she actually knows about them. One
        sentence, rewritten each time, stays the same length at the fiftieth
        donation as at the first.
        """
        if not user:
            return

        key = user.lower()
        record = self.donors.get(key, {"count": 0, "total": 0.0})
        record["count"] = record.get("count", 0) + 1
        record["total"] = round(record.get("total", 0.0) + float(amount), 2)
        record["last_amount"] = float(amount)
        record["currency"] = currency
        record["last"] = time.strftime("%Y-%m-%d")
        self.donors[key] = record
        self._save()
        print(f"[memory] {user}: donation #{record['count']}, "
              f"{currency}{record['total']} total")

    def donor_line(self, user: str) -> str:
        """One sentence about their donations, or nothing."""
        record = self.donors.get((user or "").lower())
        if not record or not record.get("count"):
            return ""

        currency = record.get("currency", "$")
        count = record["count"]
        if count == 1:
            return (f"They have donated once, "
                    f"{currency}{record.get('last_amount', 0):g}.")
        return (f"They have donated {count} times, "
                f"{currency}{record.get('total', 0):g} in total, "
                f"most recently {currency}{record.get('last_amount', 0):g}.")

    def notes_for_prompt(self, user: str) -> str:
        """
        What she knows about them, for the prompt: the prose note plus the
        donation fact.

        Separate from `get_user_notes` because that one feeds the compression
        prompt — handing the model the donation sentence as "current notes"
        would have it rewritten into prose, then counted twice, then slowly
        corrupted.
        """
        parts = [p for p in (self.get_user_notes(user), self.donor_line(user))
                 if p]
        return " ".join(parts)

    def get_user_note_compression_prompt(self, user: str) -> str:
        """Build prompt to compress/update a user's notes."""

        current = self.get_user_notes(user)

        # ONLY this person's messages. Building from the shared buffer was the
        # bug: with five viewers talking, the fifth one's notes were written
        # from a transcript containing all five, so she remembered other
        # people's personalities as theirs.
        recent_interactions = [
            f"{'Ravyn' if msg['role'] == 'assistant' else user}: {msg['content']}"
            for msg in self.histories.get(self._key(user), ())
        ]

        conversation = ("\n".join(recent_interactions)
                        if recent_interactions else "No recent interaction.")

        prompt = f"""Update these notes about the viewer "{user}" based on recent interaction. Keep it under 2 sentences. Focus on personality traits, interests, and how Ravyn feels about them.

Current notes: {current or 'New viewer, no notes yet.'}

Recent interaction:
{conversation}

Write ONLY the updated notes, nothing else."""

        return prompt

    # ---------------------------------------------------------
    # persistence
    # ---------------------------------------------------------

    def _save(self):
        # general_memory is deliberately NOT written: it is live for the
        # length of a run and nothing reads it back, so persisting it would
        # only leave a stale summary in the file to confuse the next person
        # who opens it by hand.
        data = {
            "user_notes": self.user_notes,
            "donors": self.donors,
            "last_updated": time.time(),
        }
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[memory] Save failed: {e}")

    def _load(self):
        if not MEMORY_FILE.exists():
            print("[memory] No memory file — starting fresh")
            return

        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # NOT general_memory. Every run she is a new person with no record
            # of the last one — the streamer's words, and the right call: that
            # summary is "what has been happening on this stream", so
            # restoring it carries last night's losing session into the system
            # prompt of tonight's first line. STATUS records this biting once,
            # a mock game leaving "Ravyn and multiple viewers discussed Team K
            # deaths" in there to be repeated at a live audience.
            #
            # Who people ARE does survive, which is a different thing: a
            # regular who donated last week should still be someone she knows.
            self.user_notes = data.get("user_notes", {})
            self.donors = data.get("donors", {})
            print(f"[memory] Loaded — {len(self.user_notes)} user notes, "
                  f"{len(self.donors)} donor(s). The stream summary starts "
                  f"empty by design.")
        except Exception as e:
            print(f"[memory] Load failed: {e}")