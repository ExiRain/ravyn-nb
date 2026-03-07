from pydantic import BaseModel, Field
from typing import Optional, Dict
from time import time


class RequestMessage(BaseModel):
    source: str = Field(..., description="Origin of message: user/chat/system")
    message: str = Field(..., description="Text content to send to LLM")

    priority: int = Field(
        ...,
        ge=0,
        le=10,
        description="Lower number = higher priority"
    )

    ttl_seconds: int = Field(
        ...,
        gt=0,
        description="How long message remains valid"
    )

    timestamp: float = Field(
        default_factory=time,
        description="Creation timestamp"
    )

    user: Optional[str] = Field(
        None,
        description="Username if coming from chat"
    )

    context: Optional[Dict] = Field(
        None,
        description="Optional contextual metadata"
    )

    def is_expired(self) -> bool:
        return (time() - self.timestamp) > self.ttl_seconds