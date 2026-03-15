import pika
from app.settings import get_settings
from adapters.audio.stream_api import schedule_tts
from adapters.llm.llama_server_client import run_llm

settings = get_settings()


def start_worker():

    credentials = pika.PlainCredentials(
        settings.RABBIT_USER,
        settings.RABBIT_PASS
    )

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=settings.RABBIT_HOST,
            port=settings.RABBIT_PORT,
            virtual_host=settings.RABBIT_VHOST,
            credentials=credentials
        )
    )

    channel = connection.channel()
    channel.queue_declare(queue=settings.QUEUE_REQUEST)
    channel.queue_declare(queue=settings.QUEUE_RESPONSE)

    print("RabbitMQ connected")
    print("Waiting for messages...")

    def callback(ch, method, properties, body):

        message = body.decode()
        print("Incoming prompt:", message)

        # --- LLM ---
        response = run_llm(message)
        text     = response.get("text", "")
        print("LLM response:", text)

        # --- TTS + stream to Godot ---
        # generates sentence by sentence, Ravyn starts speaking immediately
        schedule_tts(text)

        # --- send text response back ---
        channel.basic_publish(
            exchange="",
            routing_key=settings.QUEUE_RESPONSE,
            body=text
        )

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=settings.QUEUE_REQUEST,
        on_message_callback=callback
    )

    channel.start_consuming()