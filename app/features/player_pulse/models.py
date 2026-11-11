"""This feature's own table. Migration: alembic/versions/0050_player_pulse.py (renumber right before merging)."""
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


class PulseVote(Base):
    __tablename__ = "pulse_votes"
    __table_args__ = (UniqueConstraint("user_id", "game_name", "day", name="uq_pulse_votes_user_game_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    feel: Mapped[str] = mapped_column(String(12))  # loved | too_hard | too_easy
    note: Mapped[str | None] = mapped_column(String(140), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
