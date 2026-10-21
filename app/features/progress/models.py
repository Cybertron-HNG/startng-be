"""The Progress Engine's own tables. Migration: alembic/versions/0020_progress.py."""
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Run(Base):
    """One score submission (C1): every run, not only a personal best."""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_user_created", "user_id", "created_at"),
        Index("ix_runs_game_created", "game_name", "created_at"),
        Index("ix_runs_created", "created_at"),  # the retention purge
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"))
    score: Mapped[int] = mapped_column(BigInteger)  # the run's first stat, for quick sorting
    stats: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The daily tournament this run counted for, if the player had entered and the game was in it.
    tournament_id: Mapped[int | None] = mapped_column(ForeignKey("tournaments.id", ondelete="SET NULL"))
    # Held for review (score integrity): a flagged run earns no XP, awards, rank or tournament points until cleared.
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # The player's place on the game's board right after this run (only while score integrity is on).
    rank_after: Mapped[int | None] = mapped_column(Integer)


class GameOpen(Base):
    """Proof that a signed-in player opened a game (C4): when they first and last did, and how many times."""

    __tablename__ = "game_opens"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    count: Mapped[int] = mapped_column(Integer, default=1)


class ActivityDay(Base):
    """One row per player per day (Nigeria time): how many runs, and the XP earned that day."""

    __tablename__ = "activity_days"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    xp: Mapped[int] = mapped_column(Integer, default=0)


class Award(Base):
    """An item or badge a player owns (C3). Unique per player, kind and key, so awarding twice does nothing."""

    __tablename__ = "awards"
    __table_args__ = (UniqueConstraint("user_id", "kind", "key", name="uq_awards_user_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "item" or "badge"
    key: Mapped[str] = mapped_column(String(64))
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
