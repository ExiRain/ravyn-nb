from threading import Thread
import uvicorn

from app.settings import get_settings
from adapters.mq.rabbitmq import start_worker


def start_api():

    s = get_settings()

    uvicorn.run(
        "adapters.audio.stream_api:app",
        host=s.NOTEBOOK_HOST,
        port=s.API_PORT,
        log_level="info"
    )


def main():

    s = get_settings()

    print("Ravyn-Lynx notebook service starting")
    print("API_PORT:", s.API_PORT)

    api_thread = Thread(target=start_api, daemon=True)
    api_thread.start()

    start_worker()


if __name__ == "__main__":
    main()