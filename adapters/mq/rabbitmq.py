import json
import threading
import pika
from app.settings import get_settings
from adapters.audio.stream_api import schedule_tts, set_on_complete
from adapters.llm.llama_server_client import run_llm

settings = get_settings()


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

        # --- Parse JSON or fall back to plain text ---
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
            print("[worker] Skipping LLM — direct to TTS")
            schedule_tts(text)
        else:
            response = run_llm(text)
            llm_text = response.get("text", "")
            print(f"[worker] LLM: {llm_text[:80]}...")

            schedule_tts(llm_text)

            channel.basic_publish(
                exchange="",
                routing_key=settings.QUEUE_RESPONSE,
                body=llm_text,
            )

        # --- Wait for TTS to finish, then publish IDLE ---
        done_event.wait(timeout=30)
        publish_status("IDLE")

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=settings.QUEUE_REQUEST,
        on_message_callback=callback,
    )

    channel.start_consuming()