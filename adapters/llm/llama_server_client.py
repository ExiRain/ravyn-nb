import requests

LLM_URL = "http://127.0.0.1:8081/v1/chat/completions"


def run_llm(prompt: str) -> dict:

    payload = {
        "model": "local",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 200
    }

    response = requests.post(LLM_URL, json=payload)
    response.raise_for_status()

    data = response.json()

    text = data["choices"][0]["message"]["content"]

    return {
        "text": text.strip(),
        "emotion": "neutral",
        "intensity": 0.5
    }