import asyncio
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

clients = set()
event_loop = None

AUDIO_CHUNK_BYTES = 8192

# envelope state
running_peak = 1e-6
previous_env = 0.0


@app.on_event("startup")
async def startup_event():
    global event_loop
    event_loop = asyncio.get_running_loop()
    print("FastAPI event loop ready")


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


def _compute_envelope(samples: np.ndarray) -> float:
    global running_peak, previous_env

    if samples.size == 0:
        return 0.0

    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

    # adaptive normalization
    running_peak = max(running_peak * 0.995, rms)
    env = rms / running_peak if running_peak > 0 else 0.0
    env = max(0.0, min(env, 1.0))

    # smoothing
    smoothed = env * 0.65 + previous_env * 0.35
    previous_env = smoothed

    return smoothed


async def push_audio(audio_bytes: bytes):
    if not clients:
        print("No websocket clients")
        return

    print("Audio bytes:", len(audio_bytes))
    print("Connected clients:", len(clients))

    # WAV header = first 44 bytes
    if len(audio_bytes) < 44:
        print("Audio too small")
        return

    wav_header = audio_bytes[:44]
    pcm_bytes = audio_bytes[44:]

    # decode PCM for mouth envelope
    pcm_samples = np.frombuffer(pcm_bytes, dtype=np.int16)

    # keep audio and mouth messages aligned
    bytes_per_sample = 2
    samples_per_chunk = AUDIO_CHUNK_BYTES // bytes_per_sample

    for ws in list(clients):
        try:
            await ws.send_text("START")

            # first send full wav once so Godot can reconstruct/play it
            for i in range(0, len(audio_bytes), AUDIO_CHUNK_BYTES):
                await ws.send_bytes(audio_bytes[i:i + AUDIO_CHUNK_BYTES])
                await asyncio.sleep(0)

            # then send normalized mouth envelope timeline
            for i in range(0, len(pcm_samples), samples_per_chunk):
                chunk = pcm_samples[i:i + samples_per_chunk]
                env = _compute_envelope(chunk)
                await ws.send_text(f"MOUTH:{env}")
                await asyncio.sleep(0)

            await ws.send_text("END")

        except Exception as e:
            print("Client send failed:", e)
            clients.discard(ws)


def schedule_audio(audio_bytes: bytes):
    if event_loop is None:
        print("ERROR: event loop not ready")
        return

    asyncio.run_coroutine_threadsafe(
        push_audio(audio_bytes),
        event_loop
    )