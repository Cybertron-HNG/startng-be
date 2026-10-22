from datetime import datetime

from pydantic import BaseModel, Field


class PrivateTournamentIn(BaseModel):
    title: str = Field(min_length=3, max_length=60)
    games: list[str] = Field(min_length=1, max_length=5)
    hours: int = Field(ge=24, le=720)  # how long it runs, from now: 1 day to 30 days


class RunOut(BaseModel):
    rank: int
    player_id: int
    name: str
    color: str | None
    stats: dict
    points: int


class GameOut(BaseModel):
    name: str
    url: str
    cover_url: str | None
    leaderboard: list[dict]
    players: int
    top: list[RunOut]


class StandingOut(BaseModel):
    place: int
    player_id: int
    name: str
    color: str | None
    points: int
    firsts: int


class SummaryOut(BaseModel):
    """What anyone with the invite link sees."""
    code: str
    title: str
    owner: str
    starts_at: datetime
    ends_at: datetime
    finished: bool
    members: int
    games: list[str]
    joined: bool
    mine: bool  # the signed-in player made it


class PrivateTournamentOut(SummaryOut):
    """For members: the games with their boards, and the standings."""
    game_boards: list[GameOut]
    standings: list[StandingOut]
    me: StandingOut | None
