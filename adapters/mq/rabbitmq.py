import json
import re
import threading
import time
import pika
from app.settings import get_settings
from adapters.llm.llama_server_client import run_llm, run_llm_simple
from persona.context_builder import build_messages
from persona.memory import MemoryManager

settings = get_settings()
memory = MemoryManager()

_response_count_since_fufu = 0


def _ts() -> str:
    """Timestamp for logging."""
    return time.strftime("%H:%M:%S")


def _clean_for_tts(text: str) -> str:
    text = re.sub(r'[\[\(][^\]\)]{1,20}[\]\)]', '', text)
    text = re.sub(r'\*[^*]{1,30}\*', '', text)
    text = re.sub(r'  +', ' ', text).strip()
    return text


# Third-person narration about herself: "Ravyn tilts her head...".
# Matched by NAME only. "she/he + verb" is deliberately not matched — she talks
# about other people that way constantly ("he plays jungle like a bot"), so a
# pronoun rule would eat real speech.
_NARRATION_SELF = re.compile(r'\bRavyn\s+\w+s\b', re.IGNORECASE)

# Dialogue attribution after a closing quote: '"NewViewer_123," she murmurs
# to no one.' The quote-comma-pronoun-verb shape only occurs in prose, so this
# is safe where a bare pronoun rule would not be.
# The comma usually sits INSIDE the closing quote ('..._123," she murmurs'),
# so it has to be consumed here or it survives as a dangling 'NewViewer_123,.'
_DIALOGUE_TAG = re.compile(
    r',?\s*(["\u201c\u201d])\s*,?\s*(?:she|he|they)\s+\w+s\b[^.!?]*([.!?])',
    re.IGNORECASE)

_QUOTE_CHARS = '"\u201c\u201d'


def _strip_narration(text: str) -> str:
    """
    Remove prose narration so she speaks instead of describing herself.

    The model writes fiction ABOUT Ravyn rather than being her — "Ravyn tilts
    her head at the chat notification", '"NewViewer_123," she murmurs' — and
    TTS reads every word of it aloud. The system prompt forbids this and the
    model does it anyway, so it gets removed here.

    Returns "" when the whole response was narration. Saying nothing is better
    than narrating; the caller logs it so the rate is visible.
    """
    if not text:
        return text

    # strip the attribution, keep the spoken part
    text = _DIALOGUE_TAG.sub(r'\1\2', text)

    # drop whole sentences that describe her in third person
    kept = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text)
            if s.strip() and not _NARRATION_SELF.search(s)]
    text = " ".join(kept)

    # she never puts quotation marks around her own words
    for q in _QUOTE_CHARS:
        text = text.replace(q, '')

    return re.sub(r'\s+', ' ', text).strip()


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

        try:
            if skip_llm:
                # quote mode — canned line, straight through to the PC's TTS
                print(f"[{_ts()}][worker] Quote mode — no LLM")
                spoken_text = _clean_for_tts(text)
            else:
                user = context.get("user", "")
                user_notes = memory.get_user_notes(user) if user else ""

                messages = build_messages(
                    text=text, source=source, context=context,
                    history=memory.get_history(),
                    general_memory=memory.general_memory,
                    user_memory=user_notes,
                    recent_openers=memory.get_recent_openers(),
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

                spoken_text = _gate_fufu(spoken_text, source)
                spoken_text = _gate_tch(spoken_text)

                before = spoken_text
                spoken_text = _strip_narration(spoken_text)
                if spoken_text != before:
                    if spoken_text:
                        print(f"[{_ts()}][worker] Stripped narration -> {spoken_text[:60]}")
                    else:
                        print(f"[{_ts()}][worker] ALL narration, saying nothing: {before[:80]}")

                # update memory
                if spoken_text:
                    memory.add_exchange(
                        user_msg=text, assistant_msg=spoken_text,
                        source=source, user=user)

                    if memory.needs_compression():
                        _compress_memory_async(user)

        except Exception as e:
            # still fall through to publish — the PC must always get a reply
            print(f"[{_ts()}][worker] ERROR: {e}")

        response_payload = json.dumps({
            "text": spoken_text,
            "mood": mood,
            "tired": tired,
            "source": source,
            "event_type": context.get("event_type", ""),
            "lang": context.get("lang", "en"),
        })

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
