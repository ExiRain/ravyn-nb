"""
What the model was asked, and what it did with it.

    python tools/worker_report.py                  # newest worker log
    python tools/worker_report.py logs/worker-x.jsonl
    python tools/worker_report.py --prompt         # the persona as it ran
    python tools/worker_report.py --line 14        # one request, in full
    python tools/worker_report.py --game 2         # only the second game

The PC's `analyze_session.py` measures how she sounded. This measures the
pipeline behind it, and answers the questions that file cannot:

    did the direction reach the model    angle and tone present in the prompt
    is the sampler actually sampling     distinct seeds, identical answers
    what is being cut                    filters by type, and what they ate
    is history leaking into game events  history turns per source
    how slow is she                      LLM seconds, per request

The filter tally in the worker prints a rate every ten responses. This is the
same counting with the evidence attached: `narration 3` tells you it happened,
`--line 14` shows you the sentence.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def load(path: Path) -> tuple[list[dict], dict]:
    """
    Returns (line records, meta). `meta["games"]` is a list of the line-index
    ranges between `game_start` and `game_end` markers, so a log covering a
    whole evening can be read a game at a time.
    """
    lines, meta = [], {}
    games: list[dict] = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue

        kind = rec.get("kind")
        if kind == "line":
            lines.append(rec)
        elif kind == "system_prompt":
            meta["system_prompt"] = rec
        elif kind == "marker":
            if rec.get("marker") == "game_start":
                games.append({"start": len(lines), "end": None,
                              "champion": rec.get("champion", ""),
                              "iso": rec.get("iso", "")})
            elif rec.get("marker") == "game_end" and games:
                games[-1]["end"] = len(lines)

    # A game the log ends inside — crash, or a restart mid-game — runs to the
    # last line rather than being dropped.
    for game in games:
        if game["end"] is None:
            game["end"] = len(lines)

    meta["games"] = games
    return lines, meta


def scope_to_game(lines: list[dict], meta: dict, number: int) -> list[dict]:
    games = meta.get("games") or []
    if not 1 <= number <= len(games):
        print(f"No game {number} in this log ({len(games)} recorded).")
        return []
    game = games[number - 1]
    return lines[game["start"]:game["end"]]


def newest() -> Path | None:
    logs = sorted((ROOT / "logs").glob("worker-*.jsonl"))
    return logs[-1] if logs else None


def _norm(text: str) -> str:
    return re.sub(r"[^\w']+", " ", (text or "").lower()).strip()


def report(lines: list[dict], meta: dict, path: Path) -> None:
    llm_lines = [r for r in lines if not r.get("skip_llm")]

    print()
    print("=" * 72)
    print(f"  {path}")
    print("=" * 72)

    print(f"\n  requests         : {len(lines)}  "
          f"({len(llm_lines)} through the LLM)")
    sources = Counter(r.get("source", "?") for r in lines)
    print(f"  sources          : "
          + ", ".join(f"{k} {v}" for k, v in sources.most_common()))
    errors = [r for r in lines if r.get("error")]
    if errors:
        print(f"  errors           : {len(errors)}  "
              f"(first: {errors[0]['error'][:60]})")

    games = meta.get("games") or []
    if games:
        spans = ", ".join(
            f"{i + 1}: {g['end'] - g['start']} requests"
            + (f" as {g['champion']}" if g["champion"] else "")
            for i, g in enumerate(games))
        print(f"  games            : {len(games)}  ({spans})")
        print(f"                     --game N to read one on its own")

    if "system_prompt" in meta:
        print(f"  system prompt    : {meta['system_prompt']['chars']} chars "
              f"(--prompt to read it)")

    # ------------------------------------------------- did direction land?
    # The worker checks each block against the prompt it built and records a
    # flag — see app/worker_log.py. Absent flag means the PC sent no such
    # block, which is not the same as one going missing.
    def landed(flag: str) -> tuple[int, int]:
        sent = [r for r in llm_lines if flag in r]
        return sum(1 for r in sent if r[flag]), len(sent)

    angle_ok, angle_sent = landed("angle_in_prompt")
    tone_ok, tone_sent = landed("tone_in_prompt")
    situation_ok, situation_sent = landed("situation_in_prompt")

    if angle_sent or tone_sent or situation_sent:
        print("\n" + "-" * 72)
        print("  DID THE DIRECTION REACH THE MODEL")
        print("-" * 72 + "\n")
        for label, ok, sent in (("angle", angle_ok, angle_sent),
                                ("tone", tone_ok, tone_sent),
                                ("situation", situation_ok, situation_sent)):
            if not sent:
                print(f"  {label:10} never sent by the PC")
                continue
            print(f"  {label:10} {ok}/{sent} reached the prompt"
                  + ("   <-- chosen and then dropped" if ok < sent else ""))
        print("\n  If these are full and she still repeated herself, the "
              "model is ignoring\n  the direction — a model or prompt problem, "
              "not an angle-pool one.")

    # ------------------------------------------------------------ sampler
    seeds = [r.get("seed") for r in llm_lines if r.get("seed") is not None]
    if seeds:
        raws = [(_norm(r.get("raw", "")), r.get("seed")) for r in llm_lines
                if r.get("raw")]
        by_text = Counter(t for t, _ in raws)
        identical = [t for t, n in by_text.items() if n > 1 and t]

        print("\n" + "-" * 72)
        print("  IS THE SAMPLER SAMPLING")
        print("-" * 72)
        print(f"\n  distinct seeds   : {len(set(seeds))} of {len(seeds)}")
        if identical:
            print(f"  IDENTICAL ANSWERS: {len(identical)} text(s) returned more "
                  f"than once")
            for text in identical[:3]:
                these = {s for t, s in raws if t == text}
                print(f"    {len(these)} different seeds -> \"{text[:60]}\"")
            print("\n  Different seeds returning the same bytes means the "
                  "server ignored\n  the seed — the temperature jitter is all "
                  "that is varying it.")
        else:
            print("  no two answers identical — the sampler is moving")

    # ------------------------------------------------------------ filters
    removed = Counter()
    for r in llm_lines:
        removed.update(r.get("removed") or [])
    emptied = [r for r in llm_lines
               if not r.get("said") and r.get("raw")]

    print("\n" + "-" * 72)
    print("  WHAT THE FILTERS TOOK OUT")
    print("-" * 72 + "\n")
    if removed:
        for what, n in removed.most_common():
            rate = n / len(llm_lines) if llm_lines else 0
            print(f"  {what:18} {n:4}   {rate:5.0%} of responses")
    else:
        print("  nothing — she stayed in character all session")
    if emptied:
        print(f"\n  {len(emptied)} response(s) were ENTIRELY scaffolding and "
              f"she said nothing")

    # -------------------------------------------------------- prompt shape
    chars = [r["prompt_chars"] for r in llm_lines if r.get("prompt_chars")]
    if chars:
        print("\n" + "-" * 72)
        print("  PROMPT AND LATENCY")
        print("-" * 72 + "\n")
        print(f"  prompt chars     : min {min(chars)}, "
              f"median {int(statistics.median(chars))}, max {max(chars)}")

        history = Counter(r.get("history_turns", 0) for r in llm_lines)
        game_history = [r.get("history_turns", 0) for r in llm_lines
                        if r.get("source") == "game"]
        if game_history:
            worst = max(game_history)
            print(f"  history turns    : "
                  + ", ".join(f"{k}:{v}" for k, v in sorted(history.items())))
            print(f"  game events      : {worst} history turns at most"
                  + ("   <-- game events must carry NONE"
                     if worst else "   (correct — they carry none)"))

    secs = [r["llm_s"] for r in llm_lines if r.get("llm_s")]
    if secs:
        print(f"  llm seconds      : min {min(secs):.1f}, "
              f"median {statistics.median(secs):.1f}, max {max(secs):.1f}")
        retried = sum(1 for r in llm_lines if r.get("retried"))
        if retried:
            print(f"  retried (empty)  : {retried}")

    print("\n" + "=" * 72 + "\n")


def print_line(lines: list[dict], seq: int) -> None:
    match = next((r for r in lines if r.get("seq") == seq), None)
    if match is None:
        print(f"No request #{seq} in this log (1..{len(lines)})")
        return

    print()
    for key in ("seq", "iso", "req_id", "source", "event_type", "config_key",
                "angle_id", "tone", "lang", "seed", "temperature", "llm_s",
                "total_s", "prompt_chars", "history_turns", "removed"):
        if match.get(key) not in (None, "", []):
            print(f"  {key:15} {match[key]}")

    for label, key in (("--- trigger", "trigger"),
                       ("--- prompt (last message)", "framed"),
                       ("--- raw model output", "raw"),
                       ("--- before filters", "before_filters"),
                       ("--- said", "said")):
        if match.get(key):
            print(f"\n{label}\n")
            print("  " + str(match[key]).replace("\n", "\n  "))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("path", nargs="?", help="worker .jsonl (default: newest)")
    ap.add_argument("--prompt", action="store_true",
                    help="print the system prompt this run used, and stop")
    ap.add_argument("--line", type=int, metavar="SEQ",
                    help="print one request in full — prompt, raw, said")
    ap.add_argument("--game", type=int, metavar="N",
                    help="report on the Nth game in this log only")
    args = ap.parse_args()

    path = Path(args.path) if args.path else newest()
    if path is None:
        print("No worker logs yet. They are written to logs/ from startup.")
        return 1
    if not path.exists():
        print(f"No such file: {path}")
        return 1

    lines, meta = load(path)
    if not lines and "system_prompt" not in meta:
        print(f"{path}: nothing recorded")
        return 1

    if args.prompt:
        system = meta.get("system_prompt")
        if not system:
            print("No system prompt recorded — the run handled no LLM request.")
            return 1
        print(system["text"])
        return 0

    if args.line:
        print_line(lines, args.line)
        return 0

    if args.game:
        scoped = scope_to_game(lines, meta, args.game)
        if not scoped:
            return 1
        report(scoped, dict(meta, games=[]), path)
        return 0

    report(lines, meta, path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(1)
