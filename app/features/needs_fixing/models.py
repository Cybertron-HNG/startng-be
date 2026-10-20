from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import Game


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FixRequest(Base):
    """An admin marked a game "needs fixing": it's hidden until the owner fixes it and an admin puts it back.

    status: "waiting_owner" (the owner has to fix it), "owner_fixed" (the owner says it's fixed: back in the admins'
    queue), "closed" (an admin put it back). One request that isn't closed per game."""

    __tablename__ = "fix_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="waiting_owner", index=True)
    note: Mapped[str] = mapped_column(Text)  # what's wrong, for the owner (the latest one when sent back)
    owner_note: Mapped[str | None] = mapped_column(Text)  # what the owner says they fixed
    rounds: Mapped[int] = mapped_column(Integer, default=1)  # 2 and up: an admin sent it back
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    owner_fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(255))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[Game] = relationship()
