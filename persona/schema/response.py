from pydantic import BaseModel, Field
from typing import Optional, Dict


class ResponseMessage(BaseModel):
    text: str = Field(
        ...,
        description="Speech content returned by LLM"
    )

    emotion: str = Field(
        ...,
        description="Emotion tag for animation system"
    )

    intensity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Emotion intensity"
    )

    actions: Optional[Dict] = Field(
        None,
        description="Optional gesture/animation instructions"
    )

    audio_stream_id: Optional[str] = Field(
        None,
        description="Identifier for audio stream endpoint"
    )