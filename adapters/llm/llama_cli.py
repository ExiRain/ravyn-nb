import subprocess
import json

from app.settings import get_settings

settings = get_settings()


def run_llm(prompt: str) -> dict:

    cmd = [
        "/home/exiledr/AI/bin/llama-cli/llama-cli",
        "-m", str(settings.LLM_GGUF_PATH),
        "-c", str(settings.LLM_CTX),
        "--temp", str(settings.LLM_TEMP),
        "-p", prompt
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    raw_output = result.stdout.strip()

    try:
        data = json.loads(raw_output)
        return data
    except Exception:
        return {
            "text": raw_output,
            "emotion": "neutral",
            "intensity": 0.5
        }