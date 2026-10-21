from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class WordIn(BaseModel):
    word: str = Field(min_length=2, max_length=40)
    kind: Literal["block", "allow"]
    strong: bool = False  # block: also catch it inside other words


class WordOut(BaseModel):
    id: int | None  # None for the built-in lists
    word: str
    kind: str
    strong: bool
    built_in: bool
    added_by: str | None
    created_at: datetime | None


class TryIn(BaseModel):
    text: str = Field(max_length=300)


class TryOut(BaseModel):
    ok: bool
    word: str | None  # the word it caught


class FlaggedName(BaseModel):
    """A name already on start.ng that fails the filter, for admins to deal with."""
    kind: Literal["game", "game_description", "character", "creators_listing"]
    name: str
    word: str
    target: str  # the game's name, the user's id or the listing's id
    owner: str | None
