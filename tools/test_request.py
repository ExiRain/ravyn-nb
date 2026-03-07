import pika
from app.settings import get_settings

settings = get_settings()

connection = pika.BlockingConnection(
    pika.ConnectionParameters(
        host=settings.RABBIT_HOST,
        port=settings.RABBIT_PORT,
        credentials=pika.PlainCredentials(
            settings.RABBIT_USER,
            settings.RABBIT_PASS
        ),
        virtual_host=settings.RABBIT_VHOST
    )
)

channel = connection.channel()

channel.queue_declare(queue=settings.QUEUE_REQUEST)

message = input("Message for Ravyn: ")

channel.basic_publish(
    exchange="",
    routing_key=settings.QUEUE_REQUEST,
    body=message
)

print("Message sent to queue.")

connection.close()
