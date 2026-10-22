from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GameDailyStat(Base):
    """One game's numbers for one day (Nigeria time), written by the nightly rollup so history survives the 30-day
    deletion of raw plays. Aggregates only: nothing here says who played."""

    __tablename__ = "game_daily_stats"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    plays: Mapped[int] = mapped_column(Integer, default=0)  # times the game was opened from start.ng
    visitors: Mapped[int] = mapped_column(Integer, default=0)  # different people who opened it
    runs: Mapped[int] = mapped_column(Integer, default=0)  # scores sent in (the run log)
    players: Mapped[int] = mapped_column(Integer, default=0)  # different signed-in players who sent one


class CreatorProfile(Base):
    """What a creator says about themselves on their page."""

    __tablename__ = "creator_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    bio: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
