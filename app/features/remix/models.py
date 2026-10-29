"""Remix's own table. Migration: alembic/versions/0052_game_remixes.py (renumber right before merging)."""
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


class GameRemix(Base):
    """What a game's startng.json says about remixing: whether others may remix it, and which game it remixes."""

    __tablename__ = "game_remixes"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    # The game this one was remixed from (its name). Not a foreign key: the original may be renamed or removed later.
    remix_of: Mapped[str | None] = mapped_column(String(40), index=True)
