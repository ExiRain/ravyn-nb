import asyncio
import concurrent.futures
import io
import re
import numpy as np
import soundfile as sf
import pyrubberband as rb
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from phonemizer.backend import EspeakBackend
from kokoro import KPipeline

app = FastAPI()

clients    = set()
event_loop = None

AUDIO_CHUNK_BYTES = 8192
SAMPLE_RATE       = 24000
WAV_HEADER_SIZE   = 44

# =========================================================
# RAVYN VOICE PRESET
# =========================================================
RAVYN_VOICE     = "af_bella"
RAVYN_SPEED     = 0.95
RAVYN_SEMITONES = 3
PITCH_FACTOR    = 2 ** (RAVYN_SEMITONES / 12.0)

# =========================================================
# DEVICE
# =========================================================
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# =========================================================
# ENVELOPE STATE
# =========================================================
running_peak = 1e-6
previous_env = 0.0

# =========================================================
# BACKENDS
# =========================================================
_phonemizer_backend = None
_kokoro_pipeline    = None


@app.on_event("startup")
async def startup_event():
    global event_loop, _phonemizer_backend, _kokoro_pipeline

    event_loop = asyncio.get_running_loop()

    print("Loading phonemizer...")
    _phonemizer_backend = EspeakBackend(
        "en-us", preserve_punctuation=False, with_stress=False
    )

    print(f"Loading Kokoro TTS on {DEVICE}...")
    _kokoro_pipeline = KPipeline(lang_code='a', device=DEVICE)

    print(f"FastAPI ready  |  TTS device: {DEVICE}")


# =========================================================
# WEBSOCKET
# =========================================================

@app.websocket("/ws/audio")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    print("Godot connected")

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        print("Godot disconnected")
    finally:
        clients.discard(ws)


# =========================================================
# TEXT SPLITTING
# =========================================================

def split_sentences(text: str) -> list:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    result = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s.split()) > 20:
            parts = re.split(r'(?<=,)\s+', s)
            for part in parts:
                if result and len(part.split()) < 4:
                    result[-1] += " " + part
                else:
                    result.append(part)
        elif result and len(s.split()) < 4:
            result[-1] += " " + s
        else:
            result.append(s)
    return result[:3]


# =========================================================
# TTS - single sentence to wav bytes
# =========================================================

def generate_sentence(text: str) -> bytes:
    if not _kokoro_pipeline:
        print("TTS pipeline not ready")
        return b""

    chunks = []
    for _, _, audio in _kokoro_pipeline(text, voice=RAVYN_VOICE, speed=RAVYN_SPEED):
        chunks.append(audio)

    if not chunks:
        return b""

    full_audio = np.concatenate(chunks)

    if RAVYN_SEMITONES != 0:
        full_audio = rb.pitch_shift(full_audio, SAMPLE_RATE, RAVYN_SEMITONES)

    full_audio = full_audio.astype(np.float32)

    buf = io.BytesIO()
    sf.write(buf, full_audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


# =========================================================
# ENVELOPE
# =========================================================

def _compute_envelope(samples: np.ndarray) -> float:
    global running_peak, previous_env

    if samples.size == 0:
        return 0.0

    rms          = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
    running_peak = max(running_peak * 0.995, rms)
    env          = rms / running_peak if running_peak > 0 else 0.0
    env          = max(0.0, min(env, 1.0))
    smoothed     = env * 0.65 + previous_env * 0.35
    previous_env = smoothed
    return smoothed


# =========================================================
# PHONEMES
# =========================================================

def _get_phonemes(text: str, audio_duration: float) -> list:
    if not text or not _phonemizer_backend:
        return []

    try:
        result = _phonemizer_backend.phonemize([text], njobs=1)
        if not result:
            return []

        raw      = result[0].replace("|", " ").split()
        phonemes = [p.strip() for p in raw if p.strip()]

        if not phonemes:
            return []

        VOWELS  = set("aeiouæɑɐɔʊɪɛ")
        weights = []
        for p in phonemes:
            first_char = p[0] if p else "x"
            weights.append(1.5 if first_char in VOWELS else 1.0)

        total_weight = sum(weights)
        timestamps   = []
        t = 0.0
        for i, p in enumerate(phonemes):
            timestamps.append({ "p": p, "t": round(t, 4) })
            t += (weights[i] / total_weight) * audio_duration

        return timestamps

    except Exception as e:
        print("Phonemizer error:", e)
        return []


# =========================================================
# PUSH SINGLE SENTENCE TO GODOT
# =========================================================

async def push_sentence(ws, audio_bytes: bytes, text: str, is_first: bool, is_last: bool):
    if len(audio_bytes) < WAV_HEADER_SIZE:
        return

    if is_first:
        payload     = audio_bytes
        pcm_bytes   = audio_bytes[WAV_HEADER_SIZE:]
    else:
        payload     = audio_bytes[WAV_HEADER_SIZE:]
        pcm_bytes   = payload

    pcm_samples       = np.frombuffer(pcm_bytes, dtype=np.int16)
    samples_per_chunk = int((AUDIO_CHUNK_BYTES // 2) * PITCH_FACTOR)

    audio_duration = (len(pcm_samples) / SAMPLE_RATE) * PITCH_FACTOR
    phonemes       = _get_phonemes(text, audio_duration) if text else []

    try:
        if is_first:
            await ws.send_text("START")

        for i in range(0, len(payload), AUDIO_CHUNK_BYTES):
            await ws.send_bytes(payload[i:i + AUDIO_CHUNK_BYTES])
            await asyncio.sleep(0)

        for i in range(0, len(pcm_samples), samples_per_chunk):
            chunk = pcm_samples[i:i + samples_per_chunk]
            env   = _compute_envelope(chunk)
            await ws.send_text(f"MOUTH:{env}")
            await asyncio.sleep(0)

        for item in phonemes:
            await ws.send_text(f"PHONEME:{item['p']}:{item['t']}")
            await asyncio.sleep(0)

        if is_last:
            await ws.send_text("END")

    except Exception as e:
        print("Client send failed:", e)
        clients.discard(ws)


# =========================================================
# STREAMING PIPELINE
# =========================================================

async def _stream_tts_async(text: str):
    """Internal async TTS pipeline. Returns when audio is fully sent AND played."""

    if not clients:
        print("No websocket clients — skipping TTS")
        return

    if not text or not text.strip():
        print("Empty text — skipping TTS")
        return

    sentences = split_sentences(text)
    total     = len(sentences)
    print(f"Streaming {total} sentence(s)")

    any_sent = False
    total_duration = 0.0

    for idx, sentence in enumerate(sentences):
        is_first = idx == 0
        is_last  = idx == total - 1

        print(f"  [{idx+1}/{total}] {sentence[:60]}...")

        audio_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_sentence, sentence
        )

        if not audio_bytes:
            if is_last and any_sent:
                for ws in list(clients):
                    try:
                        await ws.send_text("END")
                    except Exception:
                        pass
            continue

        # calculate duration for this sentence
        pcm_bytes = audio_bytes[WAV_HEADER_SIZE:] if any_sent else audio_bytes[WAV_HEADER_SIZE:]
        pcm_samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        total_duration += (len(pcm_samples) / SAMPLE_RATE) * PITCH_FACTOR

        any_sent = True
        for ws in list(clients):
            await push_sentence(ws, audio_bytes, sentence, is_first, is_last)

    # wait for Godot to finish playing before returning
    if any_sent and total_duration > 0:
        print(f"  Waiting {total_duration:.1f}s for playback")
        await asyncio.sleep(total_duration)

    if not any_sent:
        print("No audio generated for any sentence")


# =========================================================
# PUBLIC API — Future-based, no global callback
# =========================================================

def schedule_tts(text: str) -> concurrent.futures.Future:
    """
    Schedule TTS and return a Future that resolves when audio is done.
    The worker waits on this future instead of a callback.
    """
    if event_loop is None:
        print("ERROR: event loop not ready")
        f = concurrent.futures.Future()
        f.set_result(None)
        return f

    future = asyncio.run_coroutine_threadsafe(_stream_tts_async(text), event_loop)
    return future


# legacy — kept for compatibility
def set_on_complete(callback):
    """Deprecated — use the Future returned by schedule_tts() instead."""
    pass