"""The jams feature's own tables. Migration: alembic/versions/0051_game_jams.py."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Jam(Base):
    """One jam: entries are taken from opens_at until entries_close_at, then players vote until voting_ends_at."""

    __tablename__ = "jams"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(80))
    brief: Mapped[str] = mapped_column(String(400))
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entries_close_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    voting_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JamEntry(Base):
    """A game entered in a jam (its startng.json said "jam": "<slug>" while the jam was open)."""

    __tablename__ = "jam_entries"
    __table_args__ = (UniqueConstraint("jam_id", "game_name", name="uq_jam_entries"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jam_id: Mapped[int] = mapped_column(ForeignKey("jams.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JamVote(Base):
    """One player's vote for one entry. A player may vote for several entries, once each."""

    __tablename__ = "jam_votes"
    __table_args__ = (UniqueConstraint("jam_id", "game_name", "user_id", name="uq_jam_votes"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    jam_id: Mapped[int] = mapped_column(ForeignKey("jams.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
