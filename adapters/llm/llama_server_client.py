"""
LLM client — talks to llama-server's OpenAI-compatible API.

Supports:
  - Full messages array (system + history + user)
  - Thinking mode toggle via /think and /no_think
  - Mood/tired tag extraction from response
"""

import re
import requests
from app.settings import get_settings


settings = get_settings()

LLM_URL = f"http://127.0.0.1:{settings.LLM_PORT}/v1/chat/completions"

# regex to extract mood/tired tags
TAG_PATTERN = re.compile(r'\[(mood|tired):([^\]]+)\]')


def run_llm(messages: list[dict], thinking: bool = False) -> dict:
    """
    Send a full messages array to the LLM and return parsed response.

    Args:
        messages: List of {"role": "system"/"user"/"assistant", "content": "..."}
        thinking: If True, prepend /think to enable reasoning mode

    Returns:
        {
            "text": "spoken text with tags stripped",
            "raw": "original response including tags",
            "mood": float or None,
            "tired": float or None,
        }
    """

    # inject thinking mode toggle into last user message
    if messages and messages[-1]["role"] == "user":
        prefix = "/think\n" if thinking else "/no_think\n"
        messages[-1]["content"] = prefix + messages[-1]["content"]

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": settings.LLM_TEMP,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "top_p": 0.95,
        "top_k": 20,
        "presence_penalty": 1.5,
    }

    try:
        response = requests.post(LLM_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[llm] Error: {e}")
        return {"text": "", "raw": "", "mood": None, "tired": None}

    # strip thinking tags if present (Qwen3.5 wraps reasoning in <think>...</think>)
    raw_text = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL).strip()

    # extract mood/tired tags
    mood = None
    tired = None
    for match in TAG_PATTERN.finditer(raw_text):
        tag_name = match.group(1)
        tag_value = match.group(2).strip()
        try:
            value = float(tag_value)
            if tag_name == "mood":
                mood = max(-1.0, min(1.0, value))
            elif tag_name == "tired":
                tired = max(0.0, min(1.0, value))
        except ValueError:
            pass

    # strip tags from spoken text
    spoken_text = TAG_PATTERN.sub('', raw_text).strip()

    return {
        "text": spoken_text,
        "raw": raw_text,
        "mood": mood,
        "tired": tired,
    }


def run_llm_simple(prompt: str) -> str:
    """
    Simple single-prompt call — used for memory compression.
    No history, no system prompt, just a direct question.
    """

    messages = [{"role": "user", "content": f"/no_think\n{prompt}"}]

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0.3,       # low creativity for summaries
        "max_tokens": 150,        # summaries should be short
    }

    try:
        response = requests.post(LLM_URL, json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()
        # strip any thinking tags
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        return text
    except Exception as e:
        print(f"[llm] Compression call failed: {e}")
        return ""
