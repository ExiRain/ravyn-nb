import json
import threading
import pika
from app.settings import get_settings
from adapters.audio.stream_api import schedule_tts, set_on_complete
from adapters.llm.llama_server_client import run_llm, run_llm_simple
from persona.context_builder import build_messages
from persona.memory import MemoryManager

settings = get_settings()
memory = MemoryManager()


def start_worker():

    credentials = pika.PlainCredentials(
        settings.RABBIT_USER,
        settings.RABBIT_PASS,
    )

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=settings.RABBIT_HOST,
            port=settings.RABBIT_PORT,
            virtual_host=settings.RABBIT_VHOST,
            credentials=credentials,
        )
    )

    channel = connection.channel()
    channel.queue_declare(queue=settings.QUEUE_REQUEST)
    channel.queue_declare(queue=settings.QUEUE_RESPONSE)
    channel.queue_declare(queue=settings.QUEUE_STATUS)

    print("RabbitMQ connected")
    print("Waiting for messages...")

    def publish_status(status: str):
        try:
            channel.basic_publish(
                exchange="",
                routing_key=settings.QUEUE_STATUS,
                body=status,
            )
            print(f"[status] {status}")
        except Exception as e:
            print(f"[status] Failed: {e}")

    def callback(ch, method, properties, body):

        raw = body.decode()

        # --- Parse message ---
        try:
            msg = json.loads(raw)
            text     = msg.get("text", "")
            skip_llm = msg.get("skip_llm", False)
            source   = msg.get("source", "unknown")
            mode     = msg.get("mode", "improv")
            context  = msg.get("context", {})
        except json.JSONDecodeError:
            text     = raw
            skip_llm = False
            source   = "legacy"
            mode     = "improv"
            context  = {}

        print(f"[worker] source={source}  mode={mode}  skip_llm={skip_llm}")
        print(f"[worker] text: {text[:80]}...")

        publish_status("BUSY")

        # --- TTS completion signal ---
        done_event = threading.Event()
        set_on_complete(lambda: done_event.set())

        if skip_llm:
            # quote mode — straight to TTS
            print("[worker] Skipping LLM — direct to TTS")
            schedule_tts(text)

        else:
            # --- Build context-aware prompt ---
            user = context.get("user", "")
            user_notes = memory.get_user_notes(user) if user else ""

            messages = build_messages(
                text=text,
                source=source,
                context=context,
                history=memory.get_history(),
                general_memory=memory.general_memory,
                user_memory=user_notes,
            )

            # --- LLM call ---
            response = run_llm(messages, thinking=settings.LLM_THINKING)
            spoken_text = response.get("text", "")
            mood = response.get("mood")
            tired = response.get("tired")

            print(f"[worker] Ravyn says: {spoken_text[:80]}...")
            if mood is not None:
                print(f"[worker] mood={mood}  tired={tired}")

            # --- Send mood/tired to Godot via WebSocket ---
            if mood is not None:
                _send_mood_to_godot(mood, tired)

            # --- TTS ---
            if spoken_text:
                schedule_tts(spoken_text)

                # --- Update memory ---
                memory.add_exchange(
                    user_msg=text,
                    assistant_msg=spoken_text,
                    source=source,
                    user=user,
                )

                # --- Compression check (async, non-blocking) ---
                if memory.needs_compression():
                    _compress_memory_async(user)

            # --- Send text response back ---
            channel.basic_publish(
                exchange="",
                routing_key=settings.QUEUE_RESPONSE,
                body=spoken_text,
            )

        # --- Wait for TTS, then IDLE ---
        done_event.wait(timeout=30)
        publish_status("IDLE")

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=settings.QUEUE_REQUEST,
        on_message_callback=callback,
    )

    channel.start_consuming()


def _send_mood_to_godot(mood: float, tired: float | None):
    """Send mood/tired values to Godot via the WebSocket clients."""
    from adapters.audio.stream_api import clients
    import asyncio

    async def _push():
        for ws in list(clients):
            try:
                await ws.send_text(f"MOOD:{mood}")
                if tired is not None:
                    await ws.send_text(f"TIRED:{tired}")
            except Exception:
                pass

    from adapters.audio.stream_api import event_loop
    if event_loop:
        asyncio.run_coroutine_threadsafe(_push(), event_loop)


def _compress_memory_async(active_user: str):
    """Run memory compression in a background thread so it doesn't block TTS."""

    def _do_compress():
        try:
            # compress general memory
            prompt = memory.get_compression_payload()
            summary = run_llm_simple(prompt)

            if summary:
                memory.apply_compression(summary, active_user)

            # update user notes if there was an active user
            if active_user:
                note_prompt = memory.get_user_note_compression_prompt(active_user)
                new_notes = run_llm_simple(note_prompt)
                if new_notes:
                    memory.update_user_notes(active_user, new_notes)
                    print(f"[memory] Updated notes for {active_user}: {new_notes[:60]}...")

        except Exception as e:
            print(f"[memory] Compression failed: {e}")

    thread = threading.Thread(target=_do_compress, daemon=True, name="memory-compress")
    thread.start()
