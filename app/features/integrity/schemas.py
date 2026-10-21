from datetime import datetime

from pydantic import BaseModel, Field


class FlagOut(BaseModel):
    """What a game's owner sees about a flagged run: never an email, never the admin's notes."""
    id: int
    game: str
    player_id: int
    player: str
    reason: str
    status: str  # open, cleared or confirmed
    stats: dict
    previous_best: int | None
    created_at: datetime
    reviewed_at: datetime | None


class AdminFlagOut(FlagOut):
    reviewed_by: str | None
    note: str | None


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)
