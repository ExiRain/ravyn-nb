"""
LLM client — talks to llama-server's OpenAI-compatible API.
"""

import re
import time
import requests
from app.settings import get_settings

settings = get_settings()
LLM_URL = f"http://127.0.0.1:{settings.LLM_PORT}/v1/chat/completions"


def _ts() -> str:
    return time.strftime("%H:%M:%S")

# mood/tired extraction patterns
TAG_PATTERNS_MOOD = [
    re.compile(r'\[\s*mood\s*:\s*([+-]?\d*\.?\d+)\s*\]', re.IGNORECASE),
    re.compile(r'(?:^|\s)mood\s*:\s*([+-]?\d*\.?\d+)', re.IGNORECASE),
]
TAG_PATTERNS_TIRED = [
    re.compile(r'\[\s*tired\s*:\s*([+-]?\d*\.?\d+)\s*\]', re.IGNORECASE),
    re.compile(r'(?:^|\s)tired\s*:\s*([+-]?\d*\.?\d+)', re.IGNORECASE),
]

# strip patterns — remove tags from spoken text
STRIP_PATTERNS = [
    re.compile(r'\[\s*mood\s*:\s*[^\]]*\]', re.IGNORECASE),
    re.compile(r'\[\s*tired\s*:\s*[^\]]*\]', re.IGNORECASE),
    re.compile(r'(?<!\w)mood\s*:\s*[+-]?\d*\.?\d+', re.IGNORECASE),
    re.compile(r'(?<!\w)tired\s*:\s*[+-]?\d*\.?\d+', re.IGNORECASE),
]


def _extract_value(text: str, patterns: list) -> float | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def _strip_tags(text: str) -> str:
    for pat in STRIP_PATTERNS:
        text = pat.sub('', text)
    text = re.sub(r'  +', ' ', text).strip()
    return text


def _handle_think_tags(raw: str) -> str:
    """
    Handle <think>...</think> tags from Qwen3.5.
    Extract content OUTSIDE think tags. If nothing outside, use last
    sentence from inside as fallback.
    """
    # check if think tags exist
    if '<think>' not in raw:
        return raw

    # extract content outside think tags
    outside = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

    if outside:
        return outside

    # fallback — everything was inside think tags
    # extract the think content and use the last sentence-like chunk
    think_match = re.search(r'<think>(.*?)</think>', raw, flags=re.DOTALL)
    if think_match:
        think_content = think_match.group(1).strip()
        # try to find something that looks like a spoken response
        lines = [l.strip() for l in think_content.split('\n') if l.strip()]
        if lines:
            # use the last non-empty line as the response
            print(f"[{_ts()}][llm] WARNING: all content was inside <think> tags, using last line as fallback")
            return lines[-1]

    return ""


def run_llm(messages: list[dict], thinking: bool = False, _retry: int = 0) -> dict:

    # log prompt size for debugging context overflow
    total_chars = sum(len(m.get("content", "")) for m in messages)
    print(f"[{_ts()}][llm] Prompt: {len(messages)} messages, ~{total_chars} chars")

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": settings.LLM_TEMP,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "top_p": 0.95,
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
        "presence_penalty": 1.5,
        "frequency_penalty": 0.3,
    }

    try:
        response = requests.post(LLM_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[{_ts()}][llm] Error: {e}")
        return {"text": "", "raw": "", "mood": None, "tired": None}

    print(f"[{_ts()}][llm] RAW ({len(raw_text)} chars): [{raw_text}]")

    # handle think tags
    text = _handle_think_tags(raw_text)

    # extract mood/tired before stripping
    mood = _extract_value(text, TAG_PATTERNS_MOOD)
    tired = _extract_value(text, TAG_PATTERNS_TIRED)

    if mood is not None:
        mood = max(-1.0, min(1.0, mood))
    if tired is not None:
        tired = max(0.0, min(1.0, tired))

    # strip tags from spoken text
    spoken_text = _strip_tags(text)

    # retry once on empty — small models sometimes just whiff
    if not spoken_text and _retry < 1:
        print(f"[{_ts()}][llm] Empty response — retrying (attempt {_retry + 1})")
        return run_llm(messages, thinking, _retry + 1)

    if not spoken_text:
        print(f"[{_ts()}][llm] WARNING: still empty after retry. Raw: [{raw_text}]")

    return {
        "text": spoken_text,
        "raw": raw_text,
        "mood": mood,
        "tired": tired,
    }


def run_llm_simple(prompt: str) -> str:
    """Simple call for memory compression."""

    messages = [{"role": "user", "content": prompt}]

    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 150,
    }

    try:
        response = requests.post(LLM_URL, json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()
        text = _handle_think_tags(text)
        return text
    except Exception as e:
        print(f"[{_ts()}][llm] Compression call failed: {e}")
        return ""