import json
import re
import threading
import time
import pika
from app.settings import get_settings
from app import worker_log
from adapters.llm.llama_server_client import run_llm, run_llm_simple
from persona import filters, numbers
from persona.context_builder import build_messages
from persona.memory import MemoryManager
from persona.repeats import RepeatGuard, without_copies

settings = get_settings()
memory = MemoryManager()

_response_count_since_fufu = 0


def _ts() -> str:
    """Timestamp for logging."""
    return time.strftime("%H:%M:%S")


# Character-drift counters for this worker run. Printed every so often rather
# than per response, so the rate is visible without burying the log.
TALLY = filters.Tally()
FILTER_REPORT_EVERY = 10


def _clean_for_tts(text: str) -> str:
    text = re.sub(r'[\[\(][^\]\)]{1,20}[\]\)]', '', text)
    text = re.sub(r'\*[^*]{1,30}\*', '', text)
    text = re.sub(r'  +', ' ', text).strip()
    return text


# "tch" is hers, but it only lands when it is rare. Raise this to hear it
# less, lower it to hear it more; 0 lets every one through.
TCH_COOLDOWN = 25

_responses_since_tch = TCH_COOLDOWN     # let the first one through

# Every spelling the model reaches for. A plain \btch\b misses "tchk" — the
# 'k' kills the word boundary — which is how it survived the first version of
# this gate and stayed the most audible thing she said.
_TCH = re.compile(r'\b(?:t+c*h+k*|t+s+k+)\b[\s,.…!?—–-]*', re.IGNORECASE)


def _gate_tch(text: str) -> str:
    """
    Ration "tch" rather than banning it.

    The old version stripped it only at position 0, so "Well, tch, whatever"
    sailed through — and the dismissive game template used to actively ask for
    it, on the five most frequent events in a game. The result was every other
    line opening the same way.

    Now it survives at most once per TCH_COOLDOWN responses, and never twice
    in one response.
    """
    global _responses_since_tch

    if not text:
        return text

    if not _TCH.search(text):
        _responses_since_tch += 1
        return text

    if _responses_since_tch >= TCH_COOLDOWN:
        # keep the first, drop repeats within this same response
        m = _TCH.search(text)
        text = text[:m.end()] + _TCH.sub('', text[m.end():])
        _responses_since_tch = 0
    else:
        text = _TCH.sub('', text)
        _responses_since_tch += 1

    text = re.sub(r'\s+', ' ', text).strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _gate_fufu(text: str, source: str) -> str:
    global _response_count_since_fufu

    if source == "game":
        return re.sub(r'\bfu\s*fu\b', '', text, flags=re.IGNORECASE).strip()

    if "fufu" in text.lower():
        if _response_count_since_fufu < 8:
            text = re.sub(r'\bfu\s*fu\b', '', text, flags=re.IGNORECASE).strip()
        else:
            _response_count_since_fufu = 0

    _response_count_since_fufu += 1
    return text


def _finish(raw: str, source: str) -> tuple[str, list[str]]:
    """
    Everything between the model and the TTS, counted as it goes — see
    persona/filters.py. The tally is what turns "she felt off today" into a
    number that moves when a prompt changes.
    """
    text = _gate_fufu(raw, source)
    text = _gate_tch(text)

    before = text
    result = filters.clean(text, TALLY)
    if result.changed:
        what = ", ".join(result.removed)
        if result.text:
            print(f"[{_ts()}][worker] Cleaned [{what}] -> {result.text[:60]}")
        else:
            print(f"[{_ts()}][worker] ALL {what}, saying nothing: "
                  f"{before[:80]}")

    if TALLY.responses % FILTER_REPORT_EVERY == 0:
        print(f"[{_ts()}][filters] {TALLY.summary()}")
    return result.text, list(result.removed)


# Her own lines, checked against everything she has said this session. Only
# where dropping a line costs nothing: a skipped dragon comment is never
# noticed, a repeated one is. Never chat — a viewer who asks twice is owed an
# answer twice.
REPEAT_GUARDED = {"game", "silence_filler"}

# Retried but never dropped: a game with no opening or closing word from her
# reads as broken, which is worse than a familiar phrase.
MUST_LAND = {"GameStart", "GameEnd"}

REPEATS = RepeatGuard()


_SEED_PREFIX = "Exiled's teammate "


def _seed_line(trigger: str, context: dict) -> str:
    """
    The event's trigger as something she can say herself, or "".

    The PC writes it as "<what happened>. <a quote from her own pool>", in
    English. "Exiled's teammate Sylas took Baron." is addressed to her, so the
    lead-in goes; a non-English game gets nothing, since the seed would be the
    wrong language.
    """
    if (context.get("lang") or "en") != "en":
        return ""
    seed = (trigger or "").strip()
    if seed.startswith(_SEED_PREFIX):
        seed = seed[len(_SEED_PREFIX):]
    return seed


def _avoid_repeat(line: str, messages: list, context: dict, trigger: str,
                  source: str) -> tuple[str, dict | None, str]:
    """
    (line to speak, the retry's LLM response or None, what happened or "").

    One retry, told exactly what it repeated. If that repeats too the line is
    dropped — unless it is one of MUST_LAND, which says the event's own seed
    text instead of the repeat.
    """
    given = " ".join([trigger or ""] + [
        str(context.get(k) or "") for k in ("player_notes", "situation",
                                            "teammate_words", "angle")])
    echo = REPEATS.echo(line, given)
    if not echo:
        return line, None, ""

    print(f"[{_ts()}][worker] Repeat of {echo} — asking once more")
    note = (f"\n\nYou were about to repeat {echo}, which you have already "
            f"said tonight. Say something different about this — a "
            f"different detail, in different words.")
    retry_messages = [dict(m) for m in messages]
    retry_messages[-1]["content"] = retry_messages[-1]["content"] + note

    retry = run_llm(retry_messages, thinking=settings.LLM_THINKING)
    candidate, _ = _finish(retry.get("text", ""), source)

    if candidate and not REPEATS.echo(candidate, given):
        print(f"[{_ts()}][worker] Fresh on retry: {candidate[:80]}")
        return candidate, retry, "repeat_retried"
    # Or flagged by the PC: soul and Baron. On 2026-10-30 "they just took
    # soul" was dropped as a repeat.
    if context.get("must_land") or context.get("event_type") in MUST_LAND:
        # The seed, not the repeat: on 2026-11-10 a Baron was answered with
        # an earlier line about a kill on Caitlyn, word for word, because
        # the retry had copied it too and this kept it.
        spoken = _seed_line(trigger, context) or candidate or line
        print(f"[{_ts()}][worker] Still a repeat — "
              f"{context.get('event_type')} must land, saying the seed")
        return spoken, retry, "repeat_kept"
    print(f"[{_ts()}][worker] Still a repeat — saying nothing")
    return "", retry, "repeat_dropped"


def _note_donation(context: dict) -> None:
    """
    A donation leaves a note on whoever sent it, keyed the way her notes are
    READ: user_key first (YouTube's "youtube:<channel id>"), else the login.
    Keyed by display name, a YouTube donor's note was filed where she never
    looks — and credited to any Twitch viewer of the same name.
    """
    donation = context.get("donation")
    if not isinstance(donation, dict) or not context.get("user"):
        return
    try:
        donor = context.get("user_key") or context["user"]
        memory.remember_name(donor, context["user"])
        memory.note_donation(donor, donation.get("amount", 0),
                             donation.get("currency", "$"))
    except Exception as e:
        print(f"[{_ts()}][worker] Donation note failed: {e}")


def _avoid_copying(line: str, messages: list, source: str
                   ) -> tuple[str, dict | None, str]:
    """
    (line, the retry's LLM response or None, what happened or "").

    Chat and voice: cut what she already said word for word to this same
    person (persona/repeats.py, without_copies). If nothing new is left, one
    retry told to answer the new message; if that copies too, its new part
    is all she says, and a person who spoke to her gets nothing rather than
    the same line twice.
    """
    earlier = [m.get("content", "") for m in messages[1:-1]
               if m.get("role") == "assistant"]
    if not earlier:
        return line, None, ""
    trimmed = without_copies(line, earlier)
    if trimmed == line.strip():
        return line, None, ""
    if trimmed:
        print(f"[{_ts()}][worker] Cut a copy of an earlier reply: {trimmed[:80]}")
        return trimmed, None, "copy_cut"

    print(f"[{_ts()}][worker] Whole reply was an earlier one — asking once more")
    note = ("\n\nYou were about to repeat what you already said to them, word "
            "for word. Answer this new message instead, in new words.")
    retry_messages = [dict(m) for m in messages]
    retry_messages[-1]["content"] = retry_messages[-1]["content"] + note
    retry = run_llm(retry_messages, thinking=settings.LLM_THINKING)
    candidate, _ = _finish(retry.get("text", ""), source)
    candidate = without_copies(candidate, earlier)
    return candidate, retry, "copy_retried" if candidate else "copy_dropped"


def _check_numbers(line: str, messages: list, context: dict, trigger: str,
                   source: str) -> tuple[str, dict | None, str]:
    """
    (line to speak, the retry's LLM response or None, what happened or "").

    A number in a game line that is in nothing she was given is invented —
    see persona/numbers.py. One retry, told which number; if the retry
    invents too, the sentences carrying invented numbers are cut.
    """
    # The game prompt only: the situation, the event, his notes and the
    # angle are all in it. Not the system prompt, whose own numbers (rules,
    # her persona) would excuse ones she invented.
    given = str((messages[-1] if messages else {}).get("content") or "") + \
        " " + (trigger or "")
    bad = numbers.unsupported(line, given)
    if not bad:
        return line, None, ""

    print(f"[{_ts()}][worker] Invented number(s) {bad} — asking once more")
    note = (f"\n\nYou were about to say {', '.join(bad)}, which is not in "
            f"the situation above. Only use numbers exactly as they are "
            f"written there, about the same person or team they belong to. "
            f"If you are not sure, use no number at all.")
    retry_messages = [dict(m) for m in messages]
    retry_messages[-1]["content"] = retry_messages[-1]["content"] + note

    retry = run_llm(retry_messages, thinking=settings.LLM_THINKING)
    candidate, _ = _finish(retry.get("text", ""), source)
    if candidate and not numbers.unsupported(candidate, given):
        print(f"[{_ts()}][worker] Numbers fixed on retry: {candidate[:80]}")
        return candidate, retry, "numbers_retried"

    trimmed = numbers.without_sentences_naming(candidate or line, given)
    if not trimmed and (context.get("must_land")
                        or context.get("event_type") in MUST_LAND):
        # Better a line with a wrong number than a game with no ending.
        print(f"[{_ts()}][worker] Numbers still off, kept — must land")
        return candidate or line, retry, "numbers_kept"
    print(f"[{_ts()}][worker] Cut the sentence(s) with invented numbers: "
          f"{trimmed[:80] or '(nothing left)'}")
    return trimmed, retry, "numbers_cut"


def start_worker():
    """
    LLM worker. Consumes ravyn.request, produces ravyn.response.

    This service does NOT speak. The PC owns TTS, audio streaming and the
    busy/idle state — see ravyn-lynx-p/services/response_listener.py.

    Contract: exactly ONE message is published to ravyn.response for every
    message consumed from ravyn.request, including failures and empty LLM
    output. The PC clears its busy flag on that response, so dropping one
    deadlocks the dispatcher until its watchdog fires.
    """

    credentials = pika.PlainCredentials(settings.RABBIT_USER, settings.RABBIT_PASS)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=settings.RABBIT_HOST, port=settings.RABBIT_PORT, heartbeat=600,
            virtual_host=settings.RABBIT_VHOST, credentials=credentials))

    channel = connection.channel()
    channel.queue_declare(queue=settings.QUEUE_REQUEST)
    channel.queue_declare(queue=settings.QUEUE_RESPONSE)

    # Opened before the first message: a request handled before this exists
    # is a line missing from the record. See app/worker_log.py.
    worker_log.init(settings.WORKER_LOG_DIR,
                    enabled=settings.WORKER_LOG_ENABLED)

    # Send the log to the PC as well, on the same channel: the worker is one
    # thread, and every log line is written inside the callback that owns
    # this channel. The PC files them under logs/nb/ — see
    # ravyn-lynx-p/services/log_receiver.py. The arguments must match the
    # PC's declaration exactly, or RabbitMQ refuses the second one.
    if settings.WORKER_LOG_SHIP:
        channel.queue_declare(queue=settings.QUEUE_LOG, arguments={
            "x-max-length": settings.LOG_QUEUE_MAX,
            "x-overflow": "drop-head"})

        def _ship(file_name: str, line: str) -> None:
            channel.basic_publish(
                exchange="", routing_key=settings.QUEUE_LOG,
                body=json.dumps({"file": file_name, "line": line},
                                ensure_ascii=False).encode("utf-8"))

        worker_log.get().attach_sink(_ship)
        print(f"[worker-log] Also sending to the PC on {settings.QUEUE_LOG}")

    print("RabbitMQ connected — waiting for messages")

    def callback(ch, method, properties, body):
        raw = body.decode()

        try:
            msg = json.loads(raw)
            text     = msg.get("text", "")
            skip_llm = msg.get("skip_llm", False)
            source   = msg.get("source", "unknown")
            mode     = msg.get("mode", "improv")
            context  = msg.get("context", {})
        except json.JSONDecodeError:
            text, skip_llm, source, mode, context = raw, False, "legacy", "improv", {}

        print(f"[{_ts()}][worker] source={source} mode={mode} skip_llm={skip_llm}")
        print(f"[{_ts()}][worker] text: {text[:80]}")

        spoken_text = ""
        mood = None
        tired = None

        # The PC stamps this so its record and ours join exactly; an older
        # client sends none and we make our own.
        # A donation leaves a note on whoever sent it, whether or not the
        # message itself is read — it happened, and she should know them as
        # someone who has paid for something. Before the skip_llm branch,
        # because a donation IS a quote signal: the PC sanitises the message
        # and sends it straight to TTS, so nothing below would see it.
        _note_donation(context)

        req_id = context.get("req_id") or worker_log.new_request_id()
        started = time.time()

        # The game boundaries arrive as ordinary signals. Marking them here
        # costs nothing and lets this log be read a game at a time, the way
        # the PC's is — before the record, so the first line of a game falls
        # inside it.
        if context.get("event_type") == "GameStart":
            worker_log.get().mark("game_start",
                                  champion=context.get("player_champion", ""))
        messages = []
        response = {}
        removed = []
        failure = ""

        try:
            if skip_llm:
                # quote mode — canned line, straight through to the PC's TTS
                print(f"[{_ts()}][worker] Quote mode — no LLM")
                spoken_text = _clean_for_tts(text)

                # A quote never goes near the LLM, so nothing else would set a
                # mood and her face would sit neutral through it. That is most
                # obviously wrong for the end-of-game cheer, which is supposed
                # to be the moment she visibly reacts.
                mood = context.get("mood_spike")
            else:
                user = context.get("user", "")
                # Memory is keyed per platform where the PC sends a key:
                # YouTube's "Bob" is "youtube:<channel id>", never Twitch's
                # bob. Twitch sends none, so its existing memories still key
                # by login exactly as before.
                who = context.get("user_key") or user
                memory.remember_name(who, user)
                # Prose note plus the donation fact. get_user_notes() stays
                # prose-only — it feeds the compression prompt.
                user_notes = memory.notes_for_prompt(who) if user else ""
                # Chat only: a viewer's first message tonight opens with a
                # hello. Voice is the streamer, and events have their own
                # welcome lines.
                greet = (source == "chat" and bool(user)
                         and not context.get("is_owner")
                         and memory.needs_greeting(who))

                messages = build_messages(
                    text=text, source=source, context=context,
                    # Per person, not per stream: a shared buffer meant
                    # answering one viewer while carrying another's thread.
                    # Game events carry none at all, and their own
                    # anti-repetition block — see persona/memory.py.
                    history=memory.get_history(source, who),
                    general_memory=memory.general_memory,
                    user_memory=user_notes,
                    recent_openers=memory.get_repetition_guard(source),
                    greet=greet,
                )

                response = run_llm(messages, thinking=settings.LLM_THINKING)
                spoken_text = response.get("text", "")
                mood = response.get("mood")
                tired = response.get("tired")

                # mood spike from game context
                mood_spike = context.get("mood_spike")
                if mood_spike is not None and mood is None:
                    mood = mood_spike

                print(f"[{_ts()}][worker] Ravyn: {spoken_text[:80]}")

                spoken_text, removed = _finish(spoken_text, source)

                # Before the repeat check, so a phrase that is cut anyway
                # does not cost her the whole line.
                # Idle thoughts too: after the games on 2026-11-12 her
                # silence fillers ran to 40 words of musing about clouds.
                if spoken_text and source in ("game", "silence_filler"):
                    short = filters.shorten(spoken_text,
                                            settings.GAME_MAX_WORDS)
                    if short != spoken_text:
                        print(f"[{_ts()}][worker] Too long, trimmed -> "
                              f"{short[:80]}")
                        spoken_text = short
                        removed.append("trimmed_long")

                # Enforced, not asked: the prompt's "already said" list did
                # not stop an 18.8% echo rate. See persona/repeats.py.
                if spoken_text and source in REPEAT_GUARDED:
                    spoken_text, retry, how = _avoid_repeat(
                        spoken_text, messages, context, text, source)
                    if how:
                        removed.append(how)
                    if retry:
                        response = retry
                        mood = retry.get("mood") if retry.get("mood") is not None else mood
                        tired = retry.get("tired") if retry.get("tired") is not None else tired
                # Game lines only: chat answers a person, and a viewer's
                # "what is 7 times 8" is not a game stat to verify.
                if spoken_text and source == "game":
                    spoken_text, retry, how = _check_numbers(
                        spoken_text, messages, context, text, source)
                    if how:
                        removed.append(how)
                    if retry:
                        response = retry
                        mood = retry.get("mood") if retry.get("mood") is not None else mood
                        tired = retry.get("tired") if retry.get("tired") is not None else tired
                if spoken_text and source in ("chat", "voice"):
                    spoken_text, retry, how = _avoid_copying(
                        spoken_text, messages, source)
                    if how:
                        removed.append(how)
                    if retry:
                        response = retry
                        mood = retry.get("mood") if retry.get("mood") is not None else mood
                        tired = retry.get("tired") if retry.get("tired") is not None else tired
                if spoken_text and source in REPEAT_GUARDED:
                    REPEATS.add(spoken_text)

                # update memory
                if spoken_text:
                    if source == "game":
                        # Kept out of conversation history on purpose, and out
                        # of the compression budget with it: a game produces
                        # dozens of these and they would crowd out the chat
                        # they are supposed to sit alongside.
                        if context.get("event_type") == "GameStart":
                            memory.clear_game_lines()   # before, not after
                        memory.add_game_line(spoken_text)
                    else:
                        if greet:
                            memory.mark_greeted(who)
                        memory.add_exchange(
                            user_msg=text, assistant_msg=spoken_text,
                            source=source, user=who)

                        # Per person too: notes are written from that
                        # person's messages when THEY have said enough, not
                        # when the room has.
                        if memory.needs_compression(who):
                            _compress_memory_async(who)

        except Exception as e:
            # still fall through to publish — the PC must always get a reply
            failure = str(e)
            print(f"[{_ts()}][worker] ERROR: {e}")

        # Written before the publish, so a line that failed to reach the PC is
        # still in the record — that is exactly the case worth finding later.
        worker_log.get().record(
            req_id=req_id, source=source, context=context, trigger=text,
            mode=mode, skip_llm=skip_llm, messages=messages, llm=response,
            said=spoken_text, removed=removed, mood=mood, tired=tired,
            total_s=time.time() - started, error=failure,
        )

        response_payload = json.dumps({
            "text": spoken_text,
            "mood": mood,
            "tired": tired,
            "source": source,
            "event_type": context.get("event_type", ""),
            "lang": context.get("lang", "en"),
        })

        # After the record, so her last line of the game falls inside it.
        if context.get("event_type") == "GameEnd":
            worker_log.get().mark("game_end")

        try:
            channel.basic_publish(
                exchange="", routing_key=settings.QUEUE_RESPONSE,
                body=response_payload)
            print(f"[{_ts()}][worker] Published response ({len(spoken_text)} chars)")
        except Exception as e:
            print(f"[{_ts()}][worker] Publish failed: {e}")

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=settings.QUEUE_REQUEST, on_message_callback=callback)
    channel.start_consuming()


def _compress_memory_async(active_user: str):
    def _do():
        try:
            prompt = memory.get_compression_payload()
            summary = run_llm_simple(prompt)
            if summary:
                memory.apply_compression(summary, active_user)
            if active_user:
                note_prompt = memory.get_user_note_compression_prompt(active_user)
                new_notes = run_llm_simple(note_prompt)
                if new_notes:
                    memory.update_user_notes(active_user, new_notes)
        except Exception as e:
            print(f"[memory] Compression failed: {e}")

    threading.Thread(target=_do, daemon=True, name="memory-compress").start()
