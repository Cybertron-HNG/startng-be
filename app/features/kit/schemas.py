from typing import Literal

from pydantic import BaseModel, Field


class SessionPingIn(BaseModel):
    session_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    event: Literal["start", "heartbeat", "end", "clip_shared"]
    kit: str | None = Field(default=None, max_length=16)  # the Kit's version

