"""Screenshots' own table. Migration: alembic/versions/0050_game_screenshots.py (renumber right before merging if someone
else takes 0050)."""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GameScreenshots(Base):
    """The pictures a game's startng.json lists, as paths inside the game's own folder (served by the Worker)."""

    __tablename__ = "game_screenshots"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    paths: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
