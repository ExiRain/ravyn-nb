"""
Memory manager — conversation history, compression, and per-user notes.

Short-term: Last 5 exchanges in a rolling buffer.
Long-term: LLM-compressed summary of older exchanges + per-user notes.
Persists to disk as JSON, survives restarts.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
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


class MemoryManager:

    def __init__(self):
        self.history: deque[dict] = deque(maxlen=MAX_HISTORY * 2)
        self.exchange_count = 0
        self.general_memory = ""
        self.user_notes: dict[str, str] = {}
        self.mood_attribution: dict = {}
        self.recent_openers: deque[str] = deque(maxlen=MAX_OPENING_TRACK)
        # Deliberately not persisted: it is scoped to the game being played,
        # and a restart is a new game.
        self.recent_game_lines: deque[str] = deque(maxlen=MAX_GAME_LINES)
        self._load()

    # ---------------------------------------------------------
    # history management
    # ---------------------------------------------------------

    def add_exchange(self, user_msg: str, assistant_msg: str, source: str = "", user: str = ""):
        """Add a user/assistant exchange to short-term history."""

        self.history.append({"role": "user", "content": user_msg})
        self.history.append({"role": "assistant", "content": assistant_msg})
        self.exchange_count += 1

        # track opening words for anti-repetition
        words = assistant_msg.strip().split()[:4]
        if words:
            opener = " ".join(words)
            self.recent_openers.append(opener)

    def add_game_line(self, line: str) -> None:
        """Remember what she said about the game, for anti-repetition only."""
        line = (line or "").strip()
        if line:
            self.recent_game_lines.append(line)

    def clear_game_lines(self) -> None:
        self.recent_game_lines.clear()

    def get_history(self, source: str = "") -> list[dict]:
        """
        Conversation history for the messages array.

        Empty for game events. A game reaction is not a turn in a conversation
        — nobody said anything to her — and replaying previous ones as if
        somebody had is what made her repeat herself. Her continuity across a
        game comes from the SITUATION block instead, which is both accurate and
        current, unlike a transcript of five stale prompts.
        """
        if source == "game":
            return []
        return list(self.history)

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

    def needs_compression(self) -> bool:
        """True when we've hit the exchange limit and should compress."""
        return self.exchange_count >= MAX_HISTORY

    def get_compression_payload(self) -> str:
        """Build the text payload to send to LLM for compression."""

        lines = []
        for msg in self.history:
            role = "Ravyn" if msg["role"] == "assistant" else "Viewer"
            lines.append(f"{role}: {msg['content']}")

        conversation = "\n".join(lines)

        prompt = f"""Summarize this conversation in 2-3 short sentences. Focus on: who talked, what topics came up, what mood Ravyn was in, and any notable moments. Be concise.

Previous context: {self.general_memory or 'None yet.'}

Recent conversation:
{conversation}

Write ONLY the summary, nothing else."""

        return prompt

    def apply_compression(self, summary: str, active_user: str = ""):
        """Apply the compressed summary and reset the history buffer."""

        self.general_memory = summary.strip()
        self.history.clear()
        self.exchange_count = 0
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

    def get_user_note_compression_prompt(self, user: str) -> str:
        """Build prompt to compress/update a user's notes."""

        current = self.get_user_notes(user)
        recent_interactions = []
        for msg in self.history:
            recent_interactions.append(f"{msg['role']}: {msg['content']}")

        conversation = "\n".join(recent_interactions) if recent_interactions else "No recent interaction."

        prompt = f"""Update these notes about the viewer "{user}" based on recent interaction. Keep it under 2 sentences. Focus on personality traits, interests, and how Ravyn feels about them.

Current notes: {current or 'New viewer, no notes yet.'}

Recent interaction:
{conversation}

Write ONLY the updated notes, nothing else."""

        return prompt

    # ---------------------------------------------------------
    # mood attribution
    # ---------------------------------------------------------

    def set_mood_cause(self, cause: str, who: str = ""):
        """Track what/who caused a significant mood shift."""
        self.mood_attribution = {
            "cause": cause,
            "who": who,
            "timestamp": time.time(),
        }
        self._save()

    def get_mood_cause(self) -> dict:
        return self.mood_attribution

    # ---------------------------------------------------------
    # persistence
    # ---------------------------------------------------------

    def _save(self):
        data = {
            "general_memory": self.general_memory,
            "user_notes": self.user_notes,
            "mood_attribution": self.mood_attribution,
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
            self.general_memory = data.get("general_memory", "")
            self.user_notes = data.get("user_notes", {})
            self.mood_attribution = data.get("mood_attribution", {})
            print(f"[memory] Loaded — {len(self.user_notes)} user notes, "
                  f"memory: {self.general_memory[:50] or 'empty'}...")
        except Exception as e:
            print(f"[memory] Load failed: {e}")