from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GameCatalog(Base):
    """What T1 knows about a game (C12): its category and its rating totals. One row per game, made when needed."""

    __tablename__ = "game_catalog"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    category: Mapped[str | None] = mapped_column(String(24), index=True)
    category_source: Mapped[str | None] = mapped_column(String(8))  # "file" (startng.json) or "owner" (Your games)
    rating_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rating_avg: Mapped[float | None] = mapped_column(Float)


class GameRating(Base):
    """One player's stars for one game (1 to 5). They can change it."""

    __tablename__ = "game_ratings"
    __table_args__ = (UniqueConstraint("user_id", "game_name", name="uq_game_ratings_user_game"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    stars: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship()
