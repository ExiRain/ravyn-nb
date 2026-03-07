import pika
from app.settings import get_settings


def start_worker():
    s = get_settings()

    credentials = pika.PlainCredentials(
        s.RABBIT_USER,
        s.RABBIT_PASS
    )

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=s.RABBIT_HOST,
            port=s.RABBIT_PORT,
            virtual_host=s.RABBIT_VHOST,
            credentials=credentials
        )
    )

    channel = connection.channel()

    channel.queue_declare(queue=s.QUEUE_REQUEST)
    channel.queue_declare(queue=s.QUEUE_RESPONSE)

    print("Rabbit worker connected")
    print("Waiting for messages...")

    def callback(ch, method, properties, body):
        message = body.decode()
        print("Received:", message)

        # placeholder response
        response = f"echo: {message}"

        channel.basic_publish(
            exchange="",
            routing_key=s.QUEUE_RESPONSE,
            body=response
        )

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=s.QUEUE_REQUEST,
        on_message_callback=callback
    )

    channel.start_consuming()