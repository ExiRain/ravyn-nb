"""
The notebook's half of the record.

    python tests/test_worker_log.py

The PC can prove she repeated herself. Only this side can say why: whether the
angle reached the prompt, what the model actually produced before the filters
touched it, and whether two identical answers came from two different seeds.

The same rule as everything else on this path: the contract is exactly one
response per request, and a logging bug must never be what breaks it. Every
method here swallows its own errors, and the disabled case is the common one
(`RAVYN_WORKER_LOG=0`), not an afterthought.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import worker_log                                    # noqa: E402
from tools import worker_report                               # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


SYSTEM = "You are Ravyn. You never narrate yourself."
ANGLE = "Talk about how many drakes are gone while he farms."
TONE = "Full roast. Exasperated, merciless, still his."
SITUATION = "Minute 24. He is 0/9/1. Drakes 3-0 to the enemy."


def context(**over) -> dict:
    ctx = {"event_type": "MyDeath", "config_key": "MyDeath",
           "angle_id": "my_death_drakes", "tone": "roast", "lang": "en",
           "angle": ANGLE, "tone_instruction": TONE, "situation": SITUATION,
           "req_id": "abc123abc123"}
    ctx.update(over)
    return ctx


def messages(framed: str) -> list:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": framed}]


def framed_prompt(with_angle=True, with_tone=True) -> str:
    parts = [SITUATION]
    if with_angle:
        parts.append(f"YOUR ANGLE THIS TIME: {ANGLE}")
    if with_tone:
        parts.append(TONE)
    parts.append("GAME EVENT: You died.")
    return "\n\n".join(parts)


def llm(raw="Games are won by staying alive. <mood:-0.6>", seed=11,
        text=None) -> dict:
    return {"raw": raw, "text": text if text is not None else raw,
            "seed": seed, "temperature": 0.71, "llm_s": 3.4,
            "prompt_chars": 6823, "retried": False}


def read(log) -> list[dict]:
    return [json.loads(x) for x in
            log.path.read_text(encoding="utf-8").splitlines() if x]


# ===================================================================== record
def test_records_the_whole_request():
    print("\n--- one record per request, with the prompt attached ---")
    tmp = Path(tempfile.mkdtemp())
    try:
        log = worker_log.init(tmp, enabled=True)
        log.record(req_id="abc123abc123", source="game", context=context(),
                   trigger="You died. Killed by Riven.", mode="improv",
                   skip_llm=False, messages=messages(framed_prompt()),
                   llm=llm(), said="Games are won by staying alive.",
                   removed=[], mood=-0.6, tired=0.2, total_s=3.6)

        records = read(log)
        kinds = [r["kind"] for r in records]
        check("the run is stamped", kinds[0] == "worker_start")
        check("the system prompt is kept once", kinds.count("system_prompt") == 1)
        check("one line record", kinds.count("line") == 1, str(kinds))

        rec = next(r for r in records if r["kind"] == "line")
        check("the join id is kept", rec["req_id"] == "abc123abc123")
        check("the knobs the PC chose are kept",
              (rec["angle_id"], rec["tone"]) == ("my_death_drakes", "roast"))
        check("the framed prompt is kept",
              "YOUR ANGLE THIS TIME" in rec["framed"])
        check("the raw model output is kept, tags and all",
              "<mood:-0.6>" in rec["raw"])
        check("what she said is kept",
              rec["said"] == "Games are won by staying alive.")
        check("the seed is kept", rec["seed"] == 11)
        check("the temperature actually used is kept",
              rec["temperature"] == 0.71)
        check("timings are kept", rec["llm_s"] == 3.4 and rec["total_s"] == 3.6)
        check("prompt size is kept", rec["prompt_chars"] == len(SYSTEM)
              + len(framed_prompt()))
        check("history turns are counted", rec["history_turns"] == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_direction_landing():
    print("\n--- did the angle and the tone reach the model ---")
    tmp = Path(tempfile.mkdtemp())
    try:
        log = worker_log.init(tmp, enabled=True)

        log.record(req_id="1", source="game", context=context(),
                   trigger="died", mode="improv", skip_llm=False,
                   messages=messages(framed_prompt()), llm=llm(), said="x")
        log.record(req_id="2", source="game", context=context(),
                   trigger="died", mode="improv", skip_llm=False,
                   messages=messages(framed_prompt(with_angle=False)),
                   llm=llm(), said="x")
        log.record(req_id="3", source="chat", context={"user": "someone"},
                   trigger="hi", mode="improv", skip_llm=False,
                   messages=messages("someone says: hi"), llm=llm(), said="x")

        lines = [r for r in read(log) if r["kind"] == "line"]
        check("an angle that reached the prompt is marked true",
              lines[0]["angle_in_prompt"] is True)
        check("an angle chosen and then dropped is caught",
              lines[1]["angle_in_prompt"] is False)
        check("the tone is checked separately",
              lines[1]["tone_in_prompt"] is True)
        check("the situation block is checked too",
              lines[0]["situation_in_prompt"] is True)
        check("a signal that carries no angle gets no flag",
              "angle_in_prompt" not in lines[2])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_filters_and_quotes():
    print("\n--- what was cut, and the lines that skip the model ---")
    tmp = Path(tempfile.mkdtemp())
    try:
        log = worker_log.init(tmp, enabled=True)

        log.record(req_id="1", source="game", context=context(),
                   trigger="died", mode="improv", skip_llm=False,
                   messages=messages(framed_prompt()),
                   llm=llm(raw="*She leans back* Ravyn tilts her head. Rough.",
                           text="*She leans back* Ravyn tilts her head. Rough."),
                   said="Rough.", removed=["stage_directions", "narration"])

        log.record(req_id="2", source="game", context={"event_type": "GameEnd"},
                   trigger="GG.", mode="quote", skip_llm=True, said="GG.")

        lines = [r for r in read(log) if r["kind"] == "line"]
        check("both removals are named",
              lines[0]["removed"] == ["stage_directions", "narration"])
        check("the text before the filters is kept beside the result",
              "leans back" in lines[0]["before_filters"]
              and lines[0]["said"] == "Rough.")
        check("a quote is recorded", lines[1]["skip_llm"] is True)
        check("a quote carries no prompt or seed",
              "framed" not in lines[1] and "seed" not in lines[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_never_raises():
    print("\n--- a logging bug must not break the response contract ---")
    log = worker_log.WorkerLog("", enabled=False)
    log.record(req_id="1", source="game", context={}, trigger="x",
               mode="improv", skip_llm=False)
    check("a disabled log does nothing, quietly", log.path is None)

    worker_log._LOG = None
    check("get() before init returns a disabled log", not worker_log.get().enabled)
    worker_log.get().record(req_id="1", source="x", context={}, trigger="",
                            mode="improv", skip_llm=False)

    tmp = Path(tempfile.mkdtemp())
    try:
        live = worker_log.init(tmp, enabled=True)
        live.record(req_id="1", source="game", context=None, trigger=None,
                    mode="improv", skip_llm=False, messages=[{}],
                    llm={"raw": None})
        check("a malformed request does not raise", True)
        check("ids are unique", worker_log.new_request_id()
              != worker_log.new_request_id())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================== report
def test_report_reads_it_back():
    print("\n--- the report, on a log with a known fault in it ---")
    tmp = Path(tempfile.mkdtemp())
    try:
        log = worker_log.init(tmp, enabled=True)

        # Two different seeds, byte-identical answers: the exact signature of
        # a server ignoring the per-request seed.
        for seed in (11, 22):
            log.record(req_id=str(seed), source="game", context=context(),
                       trigger="died", mode="improv", skip_llm=False,
                       messages=messages(framed_prompt()),
                       llm=llm(raw="Games are won by staying alive.", seed=seed),
                       said="Games are won by staying alive.")
        log.record(req_id="3", source="game", context=context(),
                   trigger="died", mode="improv", skip_llm=False,
                   messages=messages(framed_prompt(with_angle=False)),
                   llm=llm(raw="Ravyn tilts her head.", seed=33, text="Ravyn tilts her head."),
                   said="", removed=["narration"])

        lines, meta = worker_report.load(log.path)
        check("the lines load", len(lines) == 3, str(len(lines)))
        check("the system prompt comes back with them",
              meta["system_prompt"]["text"] == SYSTEM)

        identical = [t for t, n in
                     __import__("collections").Counter(
                         worker_report._norm(r["raw"]) for r in lines).items()
                     if n > 1]
        check("two seeds, one answer — the report can see it",
              len(identical) == 1, str(identical))

        removed = [w for r in lines for w in r.get("removed", [])]
        check("filter removals are countable", removed == ["narration"])
        check("a response cut to nothing is visible",
              any(not r["said"] and r.get("raw") for r in lines))

        worker_report.report(lines, meta, log.path)     # must not raise
        worker_report.print_line(lines, 2)
        worker_report.print_line(lines, 99)             # out of range
        check("the report runs on a real log", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    test_records_the_whole_request()
    test_direction_landing()
    test_filters_and_quotes()
    test_never_raises()
    test_report_reads_it_back()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
