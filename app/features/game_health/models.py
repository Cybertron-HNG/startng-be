from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import Game


class GameHealth(Base):
    """The latest health check of a live game.

    status: "unchecked" (new, or just updated), "ok", "failing" (the last check failed, fewer than FAILS_IN_A_ROW
    times) or "unhealthy" (it failed FAILS_IN_A_ROW times in a row: admins and its owner see it)."""

    __tablename__ = "game_health"

    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="unchecked", index=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)  # failed checks in a row
    problems: Mapped[list | None] = mapped_column(JSON)  # [{"code", "message"}] from the latest failed check
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unhealthy_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[Game] = relationship()
