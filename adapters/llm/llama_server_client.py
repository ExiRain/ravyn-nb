"""
LLM client — talks to llama-server's OpenAI-compatible API.
"""

import re
import time
import random

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


# Whether this build accepts `seed` on /v1/chat/completions. None = not yet
# known. Some llama.cpp builds reject unknown fields outright with a 400, and
# `run_llm` turns any request failure into an empty response — which is total
# silence, on every single line, with only one log line to say why. So the
# first rejection downgrades the payload for the rest of the session instead.
_SEED_SUPPORTED: bool | None = None

# Fields that are nice to have and not worth going mute over.
_OPTIONAL_FIELDS = ("seed",)


def _post(payload: dict) -> str | None:
    """
    One request, with a retry that drops the optional fields.

    Returns None only when the server genuinely could not answer — never
    because of a parameter it does not recognise.
    """
    global _SEED_SUPPORTED

    if _SEED_SUPPORTED is False:
        payload = {k: v for k, v in payload.items()
                   if k not in _OPTIONAL_FIELDS}

    try:
        response = requests.post(LLM_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        if _SEED_SUPPORTED is None and "seed" in payload:
            _SEED_SUPPORTED = True
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        if not any(f in payload for f in _OPTIONAL_FIELDS):
            print(f"[{_ts()}][llm] Error: {e}")
            return None

        print(f"[{_ts()}][llm] Request rejected ({e}) — retrying without "
              f"{'/'.join(_OPTIONAL_FIELDS)}")

    # Second attempt, stripped. If this works the field was the problem, and
    # it stays off for the rest of the session rather than costing two
    # requests every time.
    stripped = {k: v for k, v in payload.items() if k not in _OPTIONAL_FIELDS}
    try:
        response = requests.post(LLM_URL, json=stripped, timeout=30)
        response.raise_for_status()
        data = response.json()
        _SEED_SUPPORTED = False
        print(f"[{_ts()}][llm] This build does not accept a per-request seed. "
              f"Dropped for the session; the temperature jitter still varies "
              f"her output.")
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[{_ts()}][llm] Error: {e}")
        return None


def run_llm(messages: list[dict], thinking: bool = False, _retry: int = 0) -> dict:

    # log prompt size for debugging context overflow
    total_chars = sum(len(m.get("content", "")) for m in messages)
    seed = random.randint(0, 2**31 - 1)
    # The seed is logged so a repeated answer can be told apart from a stale
    # process: two identical replies with two different seeds means the server
    # ignored it, while no seed in the log at all means this file is not the
    # one running.
    print(f"[{_ts()}][llm] Prompt: {len(messages)} messages, ~{total_chars} chars, "
          f"seed={seed}")

    payload = {
        "model": "local",
        "messages": messages,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "top_p": 0.95,
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
        "presence_penalty": 1.5,
        "frequency_penalty": 0.3,
        # Explicit, and different every time.
        #
        # Without it llama-server reuses one seed per slot, which makes the
        # sampler deterministic: asking the same question twice returned a
        # BYTE-IDENTICAL answer, mood tags and all, despite temperature 0.7.
        # The prompt was not identical — history had grown by an exchange —
        # so temperature was doing nothing at all. Every response since the
        # server started was the single most likely continuation.
        "seed": seed,

        # Belt and braces, because `seed` is an OpenAI-compat field and not
        # every llama.cpp build applies it per request on /v1/chat/completions.
        # A jitter this small is inaudible in her voice, but it perturbs the
        # softmax enough that a fixed seed can no longer land on the same token
        # sequence twice. If the seed IS honoured this changes nothing.
        "temperature": round(settings.LLM_TEMP + random.uniform(-0.05, 0.05), 3),
    }

    raw_text = _post(payload)
    if raw_text is None:
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