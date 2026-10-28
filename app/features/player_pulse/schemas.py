from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Feel = Literal["loved", "too_hard", "too_easy"]


class VoteIn(BaseModel):
    feel: Feel
    note: str | None = Field(default=None, max_length=140)


class VoteOut(BaseModel):
    feel: Feel
    note: str | None


class NoteOut(BaseModel):
    feel: Feel
    note: str
    at: datetime


class PulseOut(BaseModel):
    game: str
    days: int
    answers: int
    loved: int
    too_hard: int
    too_easy: int
    loved_pct: int  # 0 when there are no answers
    notes: list[NoteOut]  # the latest, newest first; never who wrote them
