"""This feature's table. Migration: alembic/versions/0050_live_moments.py (renumber right before merging)."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


class LiveMoment(Base):
    """Something worth showing that just happened. kind "record": user_id took #1 on game_name."""

    __tablename__ = "live_moments"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
