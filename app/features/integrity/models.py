"""Score integrity's own table. Migration: alembic/versions/0022_score_flags.py."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScoreFlag(Base):
    """A run held for review because it looks too good to be true (service.outlier_reason). While it is open the run
    counts nowhere: not on the board, not in tournaments, not for XP, awards or ranks."""

    __tablename__ = "score_flags"
    __table_args__ = (Index("ix_score_flags_game_status", "game_name", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))  # None when the Progress Engine is off
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"))
    stats: Mapped[dict] = mapped_column(JSON)  # the flagged run's stats
    previous_best: Mapped[int | None] = mapped_column(BigInteger)  # the player's best first stat before this run
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open, cleared, confirmed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))  # the admin's email
    note: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship()
