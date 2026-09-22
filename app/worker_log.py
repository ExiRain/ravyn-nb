"""
The other half of the record — what the model was actually asked, and what it
actually answered.

The PC logs what she said (`ravyn-lynx-p/orchestrator/session_log.py`). That
is enough to prove she repeated herself and to name the angle that produced
it, and not enough to say why. Four things only ever existed as stdout in the
WORKER pane, and scrolled away with it:

  * the prompt — you got `~6823 chars, seed=272306291`, not the text, so you
    could not check whether the angle was even in it
  * the seed, per request
  * her raw output, before the filters. The PC receives the cleaned text and
    cannot know what was cut
  * which filter fired on which line

A live session produced four near-identical death roasts under three
different angles. Whether the model ignored the direction, or the direction
never reached it, or a filter removed the part that differed, is a question
only this file can answer.

One JSON line per request, in `logs/worker-<date>-<time>.jsonl`. The system
prompt is written once at startup rather than on every record — it is
identical each time and would be nine tenths of the file.

The worker is a single-threaded pika consumer handling one message at a time,
so there is no correlation problem here: the record is opened and closed
inside one callback. Nothing in this file is allowed to raise — the contract
is one response per request, and a logging bug must never be what breaks it.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class WorkerLog:
    """Append-only JSONL, flushed per record."""

    def __init__(self, directory: Path | str, enabled: bool = True):
        self.enabled = enabled
        self.path: Path | None = None
        self._seq = 0
        self._started = time.time()
        self._system_prompt_written = False

        if not enabled:
            return

        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._started))
            self.path = directory / f"worker-{stamp}.jsonl"
            self._write({"kind": "worker_start", "t": self._started,
                         "iso": _iso(self._started)})
            print(f"[worker-log] Recording to {self.path}")
        except Exception as e:
            self.enabled = False
            print(f"[worker-log] Disabled — could not open log: {e}")

    # -----------------------------------------------------------------

    def record(self, *, req_id: str, source: str, context: dict,
               trigger: str, mode: str, skip_llm: bool,
               messages: list | None = None, llm: dict | None = None,
               said: str = "", removed: list | None = None,
               mood=None, tired=None, total_s: float = 0.0,
               error: str = "") -> None:
        """
        One request, start to finish. Called once per callback, after the
        response has been built and before it is published.
        """
        if not self.enabled:
            return
        try:
            self._seq += 1
            llm = llm or {}
            messages = messages or []

            record = {
                "kind": "line",
                "seq": self._seq,
                "t": time.time(),
                "iso": _iso(),
                "req_id": req_id,
                "source": source,
                "mode": mode,
                "skip_llm": skip_llm,
                "event_type": context.get("event_type", ""),
                "config_key": context.get("config_key", ""),
                "angle_id": context.get("angle_id", ""),
                "tone": context.get("tone", ""),
                "lang": context.get("lang", ""),
                "user": context.get("user", ""),
                "trigger": trigger,
                "said": said,
                "removed": list(removed or []),
                "mood": mood,
                "tired": tired,
                "total_s": round(total_s, 2),
            }

            if messages:
                # The system prompt is the same every time; the last user
                # message is the part that differs, and the part worth
                # checking against what she answered. It carries the
                # SITUATION block, the ANGLE and the TONE verbatim.
                self._write_system_prompt(messages)
                framed = messages[-1].get("content", "")
                record["framed"] = framed

                # Did the direction actually reach the model? Checked here,
                # where the context the PC sent and the prompt that was built
                # from it are both in hand. Guessing it later from block
                # headings does not work: the angle has one ("YOUR ANGLE THIS
                # TIME"), the tone and the situation are injected as bare
                # text. A prefix match survives reformatting.
                for key, flag in (("angle", "angle_in_prompt"),
                                  ("tone_instruction", "tone_in_prompt"),
                                  ("situation", "situation_in_prompt")):
                    value = context.get(key)
                    if value:
                        record[flag] = str(value)[:40] in framed
                record["prompt_messages"] = len(messages)
                record["prompt_chars"] = sum(
                    len(m.get("content", "")) for m in messages)
                # Everything between the system prompt and the current turn
                # is replayed history — the thing that made her repeat
                # herself once before.
                record["history_turns"] = max(0, len(messages) - 2)

            if llm:
                record["raw"] = llm.get("raw", "")
                record["seed"] = llm.get("seed")
                record["temperature"] = llm.get("temperature")
                record["llm_s"] = llm.get("llm_s")
                record["retried"] = llm.get("retried", False)

            # What the filters took out, in full. "narration 3" in the tally
            # tells you the rate; this tells you which sentence.
            if record["removed"] and llm.get("text") is not None:
                record["before_filters"] = llm.get("text", "")

            if error:
                record["error"] = error

            self._write(record)
        except Exception as e:
            print(f"[worker-log] record() failed: {e}")

    def _write_system_prompt(self, messages: list) -> None:
        """Once per run: her persona exactly as it was in effect."""
        if self._system_prompt_written:
            return
        self._system_prompt_written = True
        system = next((m.get("content", "") for m in messages
                       if m.get("role") == "system"), "")
        if system:
            self._write({"kind": "system_prompt", "t": time.time(),
                         "iso": _iso(), "chars": len(system),
                         "text": system})

    def _write(self, record: dict) -> None:
        if not self.path:
            return
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[worker-log] write failed: {e}")


def new_request_id() -> str:
    """
    Fallback when the PC did not send one.

    The PC stamps `context["req_id"]` so its record and this one can be joined
    exactly. An older client sends nothing, and a local id keeps this file
    self-consistent — the two halves then join on time, less precisely.
    """
    return uuid.uuid4().hex[:12]


def _iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t or time.time()))


# ---------------------------------------------------------------------
# module singleton — the worker is one callback in one thread, and threading
# a log object through it buys nothing.
# ---------------------------------------------------------------------

_LOG: WorkerLog | None = None


def init(directory: Path | str, enabled: bool = True) -> WorkerLog:
    global _LOG
    _LOG = WorkerLog(directory, enabled=enabled)
    return _LOG


def get() -> WorkerLog:
    """Never None: an uninitialised log is a disabled one, not a crash."""
    global _LOG
    if _LOG is None:
        _LOG = WorkerLog("", enabled=False)
    return _LOG
