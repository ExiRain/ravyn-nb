import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

clients = set()
event_loop = None

CHUNK_SIZE = 8192


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup_event():
    global event_loop
    event_loop = asyncio.get_running_loop()
    print("FastAPI event loop ready")


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
# AUDIO PUSH
# =========================================================

async def push_audio(audio_bytes: bytes):

    if not clients:
        print("No websocket clients")
        return

    print("Audio bytes:", len(audio_bytes))
    print("Connected clients:", len(clients))

    for ws in list(clients):

        try:

            await ws.send_text("START")

            for i in range(0, len(audio_bytes), CHUNK_SIZE):
                await ws.send_bytes(audio_bytes[i:i + CHUNK_SIZE])
                await asyncio.sleep(0)

            await ws.send_text("END")

        except Exception as e:
            print("Client send failed:", e)
            clients.discard(ws)


# =========================================================
# THREAD SAFE SCHEDULER
# =========================================================

def schedule_audio(audio_bytes: bytes):

    if event_loop is None:
        print("ERROR: event loop not ready")
        return

    asyncio.run_coroutine_threadsafe(
        push_audio(audio_bytes),
        event_loop
    )