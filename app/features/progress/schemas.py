from datetime import datetime

from pydantic import BaseModel


class ProgressOut(BaseModel):
    runs: int  # score submissions, not only personal bests
    distinct_games: int
    current_streak: int  # days in a row (Nigeria time); alive until the end of the day after the last one played
    best_streak: int
    streak_freezes: int  # each one saves the streak from one missed day (earned every 7 days, up to 2)
    xp: int
    level: int
    tournament_wins: int
    ranks: dict[str, int]  # game -> place, once held across the last two runs there (needs score integrity on)


class PublicProgressOut(BaseModel):
    """What anyone may see about a player: no XP, never an email."""
    runs: int
    distinct_games: int
    current_streak: int
    best_streak: int
    level: int
    tournament_wins: int


class AwardOut(BaseModel):
    kind: str  # "item" or "badge"
    key: str
    earned_at: datetime
