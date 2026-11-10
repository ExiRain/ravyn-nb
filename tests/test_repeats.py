"""
Has she already said this tonight — enforced, not asked.

    python tests/test_repeats.py

The prompt already told her "YOU HAVE ALREADY SAID THESE" with her last six
game lines, and the PC still measured an 18.8% echo rate on 2026-10-25: every
phrase patched out was replaced by a new one. These pin the check that runs
after generation instead, and the worker's one-retry-then-drop policy.

The detector was tuned on those real sessions: it flags 23% and 18% of their
lines, and the phrases below are taken from them.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The worker imports RabbitMQ and the LLM client; neither is needed here.
sys.modules["pika"] = types.ModuleType("pika")
llm = types.ModuleType("adapters.llm.llama_server_client")
llm.run_llm = lambda *a, **k: {"text": ""}
llm.run_llm_simple = lambda *a, **k: ""
sys.modules["adapters.llm.llama_server_client"] = llm

from persona.repeats import RepeatGuard                   # noqa: E402
import adapters.mq.rabbitmq as worker                     # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


EARLIER = ("You really do treat mid like a playground while these creatures "
           "feast on their own team, spinning circles just to keep the game "
           "moving for everyone else.")


def test_the_detector():
    print("\n--- what counts as a repeat ---")
    g = RepeatGuard()
    g.add(EARLIER)

    echo = g.echo("Walking into that was a choice. Their own team spinning "
                  "circles just to keep you company.")
    check("a five-word run of her own phrasing is caught", bool(echo), echo)
    check("and the description names the phrase",
          "own team spinning circles" in echo, echo)

    check("a genuinely different line passes",
          g.echo("Two kills on Jhin, and the CS line is clean.") == "")

    g.add("Ekko finally decided to show up while the rest of them slept.")
    check("ordinary English shared between lines is not a repeat",
          g.echo("Your CS is cleaner than the rest of their whole team.") == "",
          g.echo("Your CS is cleaner than the rest of their whole team."))

    g.add("Karma's down, though honestly at five minutes I think she just "
          "needed a nap before the real fight started.")
    near = ("Karma's down again, though honestly at this stage I think she "
            "just needed a nap before the real fight started.")
    check("the same line with a word changed is caught", bool(g.echo(near)))


def test_what_she_was_given_does_not_count():
    print("\n--- saying back what he told her is not a repeat ---")
    notes = "I'm genuinely tryharding on her right now, Ashe is my pick"
    g = RepeatGuard()
    g.add("You claim you're genuinely tryharding on Ashe right now, fine.")
    check("quoting his notes again at the next game start is allowed",
          g.echo("You say you're genuinely tryharding on Ashe right now, so "
                 "prove it.", notes) == "")

    team = "your permanently bronze friends"
    g.add("So Graves is down to join the rest of your permanently bronze "
          "friends.")
    check("the teammate words he hands her are not her catchphrase",
          g.echo("Caitlyn finally got one, unlike your permanently bronze "
                 "friends.", team) == "")


def test_the_worker_policy():
    print("\n--- one retry, then silence — except where she must speak ---")
    sent: list[list] = []

    def answer(text):
        def run(messages, **k):
            sent.append(messages)
            return {"text": text, "mood": 0.3, "tired": 0.1}
        return run

    messages = [{"role": "system", "content": "S"},
                {"role": "user", "content": "A dragon fell."}]
    repeat = "Their own team spinning circles just to keep busy."

    worker.REPEATS = RepeatGuard()
    worker.REPEATS.add(EARLIER)

    worker.run_llm = answer("The drake goes to them, and nobody even looked.")
    line, retry, how = worker._avoid_repeat(repeat, messages,
                                            {"event_type": "DragonKill"},
                                            "A dragon fell.", "game")
    check("a repeat is asked for once more", len(sent) == 1, str(len(sent)))
    check("and the retry is told exactly what it repeated",
          "own team spinning circles" in sent[0][-1]["content"],
          sent[0][-1]["content"][-160:])
    check("without touching the caller's messages",
          messages[-1]["content"] == "A dragon fell.")
    check("a fresh retry is what she says",
          line.startswith("The drake") and how == "repeat_retried", how)

    sent.clear()
    worker.run_llm = answer(repeat)
    line, _, how = worker._avoid_repeat(repeat, messages,
                                        {"event_type": "DragonKill"},
                                        "A dragon fell.", "game")
    check("a retry that repeats too is dropped",
          line == "" and how == "repeat_dropped", f"{line!r} {how}")

    line, _, how = worker._avoid_repeat(repeat, messages,
                                        {"event_type": "GameStart"},
                                        "Game started.", "game")
    check("but a game start is never left silent",
          line and how == "repeat_kept", f"{line!r} {how}")
    check("and what it says is the seed, not the repeat",
          line == "Game started.", repr(line))

    line, _, how = worker._avoid_repeat(repeat, messages,
                                        {"event_type": "DragonKill",
                                         "must_land": True},
                                        "They took soul.", "game")
    check("nor anything the PC flags must_land — soul, Baron",
          line == "They took soul." and how == "repeat_kept",
          f"{line!r} {how}")

    line, _, how = worker._avoid_repeat(
        repeat, messages, {"event_type": "BaronKill", "must_land": True},
        "Exiled's teammate Sylas took Baron. Did you just steal that?", "game")
    check("the seed is said in her voice, without the lead-in written to her",
          line == "Sylas took Baron. Did you just steal that?", repr(line))

    line, _, how = worker._avoid_repeat(
        repeat, messages,
        {"event_type": "BaronKill", "must_land": True, "lang": "ru"},
        "The enemy Elise took Baron.", "game")
    check("a Russian game keeps the retry rather than an English seed",
          line == repeat and how == "repeat_kept", f"{line!r} {how}")

    sent.clear()
    line, retry, how = worker._avoid_repeat(
        "Two kills on Jhin, and the CS line is clean.", messages,
        {"event_type": "MyKill"}, "You got a kill.", "game")
    check("a fresh line costs nothing — no second call",
          not sent and retry is None and how == "", how)

    check("chat is never guarded — a viewer who asks twice gets two answers",
          "chat" not in worker.REPEAT_GUARDED and "twitch" not in
          worker.REPEAT_GUARDED)


FIRST = ("determined811... another name in the logbook that I won't forget "
         "yet. You caught enough of me to know I'm not a decoration on your "
         "screen.")
SECOND = ("After work? That's a long time for someone who likes to hunt "
          "before bed. We'll see if you're still awake when the stream "
          "starts again.")


def thread(*replies):
    msgs = [{"role": "system", "content": "persona"}]
    for r in replies:
        msgs += [{"role": "user", "content": "viewer says something"},
                 {"role": "assistant", "content": r}]
    return msgs + [{"role": "user", "content": "Let's connect on discord"}]


def test_she_does_not_paste_her_old_replies():
    # 2026-11-09: to "Let's connect on discord" the model returned its two
    # earlier replies to that viewer pasted together; he heard the first
    # one again, word for word.
    print("\n--- copying herself to one person ---")
    from persona.repeats import without_copies
    check("the real paste is cut to nothing",
          without_copies(FIRST + " " + SECOND, [FIRST, SECOND]) == "")
    check("a new sentence after a paste survives",
          without_copies(FIRST + " Discord? Ask him.", [FIRST])
          == "Discord? Ask him.")
    check("a recurring 'Hah.' in an original reply is left alone",
          without_copies("Hah. Discord? Not my thing.", ["Hah. Fine."])
          == "Hah. Discord? Not my thing.")

    worker.run_llm = lambda *a, **k: {"text": "Discord? I live here, not there."}
    line, retry, how = worker._avoid_copying(FIRST + " " + SECOND,
                                             thread(FIRST, SECOND), "chat")
    check("a whole-paste reply gets one retry, and the retry is said",
          line == "Discord? I live here, not there." and how == "copy_retried",
          f"{how}: {line}")

    worker.run_llm = lambda *a, **k: {"text": FIRST}
    line, _, how = worker._avoid_copying(FIRST, thread(FIRST), "chat")
    check("if the retry copies too, she says nothing rather than repeat",
          line == "" and how == "copy_dropped", f"{how}: {line}")

    line, retry, how = worker._avoid_copying("Discord? No.", thread(FIRST),
                                             "chat")
    check("an original reply passes untouched, no retry",
          line == "Discord? No." and retry is None and how == "")


def main():
    test_she_does_not_paste_her_old_replies()
    test_the_detector()
    test_what_she_was_given_does_not_count()
    test_the_worker_policy()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
