from datetime import datetime

from pydantic import BaseModel, Field


class MarkIn(BaseModel):
    note: str = Field(min_length=5, max_length=1000)  # what's wrong, shown to the owner


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class FixRequestOut(BaseModel):
    id: int
    game: str
    game_url: str
    cover_url: str | None
    owner: str | None  # for admins: their name or email; never sent to the owner's own view
    status: str
    note: str
    owner_note: str | None
    rounds: int
    created_by: str | None  # for admins only
    automatic: bool = False  # marked by start.ng's health check, not by an admin (F8)
    created_at: datetime
    owner_fixed_at: datetime | None
