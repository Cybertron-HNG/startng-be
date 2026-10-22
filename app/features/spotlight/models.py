from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GameOfTheWeek(Base):
    """The editorial pick for one week (Monday to Sunday, Nigeria time). Chosen in the admin Spotlight tab."""

    __tablename__ = "game_of_the_week"

    week_start: Mapped[date] = mapped_column(Date, primary_key=True)  # the Monday
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    note: Mapped[str | None] = mapped_column(String(300))  # why it is the pick
    picked_by: Mapped[str] = mapped_column(String(255))
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DailyChallenge(Base):
    """One personal goal a day on one game ("Reach 600 in Relic Hunter"), made the first time it is asked for that day."""

    __tablename__ = "daily_challenges"

    day: Mapped[date] = mapped_column(Date, primary_key=True)  # Nigeria's day
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"))
    stat_key: Mapped[str] = mapped_column(String(24))  # the game's first leaderboard stat
    goal: Mapped[int] = mapped_column(Integer)
    runs_seen: Mapped[int] = mapped_column(Integer)  # how many past runs the goal was worked out from
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChallengeCompletion(Base):
    """A player who reached the day's goal. One per player per day: it can't be completed twice."""

    __tablename__ = "daily_challenge_completions"

    day: Mapped[date] = mapped_column(Date, ForeignKey("daily_challenges.day", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
