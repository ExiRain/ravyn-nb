from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio

app = FastAPI()

audio_queue: asyncio.Queue = asyncio.Queue()


async def audio_generator():
    while True:
        chunk = await audio_queue.get()
        if chunk is None:
            break
        yield chunk


@app.get("/stream/{stream_id}")
async def stream_audio(stream_id: str):
    return StreamingResponse(audio_generator(), media_type="audio/wav")