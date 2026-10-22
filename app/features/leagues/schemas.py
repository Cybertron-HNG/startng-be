from datetime import datetime

from pydantic import BaseModel


class PlayerOut(BaseModel):
    """A player as others may see them: name and character only, never the email."""
    id: int
    name: str | None
    character: dict | None


class RowOut(BaseModel):
    place: int | None  # None: no counted runs yet
    player: PlayerOut
    points: int
    games: int
    zone: str  # "up", "down", "stay" or "out"
    me: bool


class TableOut(BaseModel):
    track: str
    starts_at: datetime
    ends_at: datetime  # the countdown
    tier: str  # "bronze", "silver", "gold" or "diamond"
    group: int | None  # None: they haven't played this round yet (their first run seats them)
    waiting: bool  # fewer than 10 players: nobody moves yet
    points_to_promotion: int | None  # None: in the promotion zone already, top tier, or waiting
    me: RowOut | None
    rows: list[RowOut]


class TrackOut(BaseModel):
    track: str
    starts_at: datetime
    ends_at: datetime
    tier: str
    group: int | None
    me: RowOut | None
    top: list[RowOut]  # the top 3 of their group


class LeaguesOut(BaseModel):
    tracks: list[TrackOut]


class HistoryRowOut(BaseModel):
    starts_at: datetime
    ends_at: datetime
    tier: str
    place: int | None
    points: int
    result: str  # "up", "down", "stay" or "out"


class HistoryOut(BaseModel):
    track: str
    rounds: list[HistoryRowOut]
