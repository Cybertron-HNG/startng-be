"""Tips' own table. Migration: alembic/versions/0053_tips.py (renumber right before merging)."""
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import User


class Tip(Base):
    """One player's coins to the creator of a game. The coins move in the wardrobe's ledger (reasons tip_out and tip_in,
    ref "tip:<id>"); this row keeps who, which game, the note, and the day for the daily limits."""

    __tablename__ = "tips"
    __table_args__ = (Index("ix_tips_from_day", "from_user_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(140))
    day: Mapped[date] = mapped_column(Date)  # start.ng's day (the wardrobe's), for the daily limits
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sender: Mapped[User] = relationship(foreign_keys=[from_user_id])
