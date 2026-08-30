"""
Ravyn-Lynx notebook service — LLM only.

Consumes ravyn.request, runs the LLM, publishes ravyn.response.
TTS, audio streaming to Godot and busy/idle state all live on the PC
(ravyn-lynx-p). Nothing here loads a speech model, which keeps the
whole 4070 available to llama-server.
"""

from app.settings import get_settings
from adapters.mq.rabbitmq import start_worker


def main():

    s = get_settings()

    print("Ravyn-Lynx notebook service starting (LLM only)")
    print("Rabbit:", f"{s.RABBIT_HOST}:{s.RABBIT_PORT}")
    print("LLM:   ", f"127.0.0.1:{s.LLM_PORT}")

    start_worker()


if __name__ == "__main__":
    main()
