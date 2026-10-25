from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import Game


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GameReview(Base):
    """An admin's look at a new game before it's listed (F2). A game with no row is approved: every game that was
    live before reviews existed, and every game published while the feature was off.

    status: "pending" (waiting for an admin), "approved" (listed), "rejected" (stays unlisted until its owner pushes
    a fix, which makes it pending again)."""

    __tablename__ = "game_reviews"

    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    reason: Mapped[str | None] = mapped_column(String(30))  # why it was rejected (service.REJECT_REASONS)
    note: Mapped[str | None] = mapped_column(Text)  # the admin's words to the owner
    rounds: Mapped[int] = mapped_column(Integer, default=1)  # 2 and up: sent again after a rejection
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(255))

    game: Mapped[Game] = relationship()
