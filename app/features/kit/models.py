"""The Kit's own table. Migration: alembic/versions/0038_kit_game_sessions.py.

Not to be confused with security.create_game_session, which is a key a game uses to call the player API. A row here
is one play session: from the moment a game opens until the player leaves it."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlaySession(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (Index("ix_game_sessions_game_started", "game_name", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True)  # made by kit.js, one per page load
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)  # None for guests
    kit_version: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
