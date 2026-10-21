from datetime import datetime

from pydantic import BaseModel


class ProgressOut(BaseModel):
    runs: int  # score submissions, not only personal bests
    distinct_games: int
    current_streak: int  # days in a row (Nigeria time); alive until the end of the day after the last one played
    best_streak: int
    xp: int
    level: int
    tournament_wins: int


class AwardOut(BaseModel):
    kind: str  # "item" or "badge"
    key: str
    earned_at: datetime
