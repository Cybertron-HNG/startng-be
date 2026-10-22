from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GameRank(Base):
    """How well a game is doing right now (C10): one row per game, rewritten every hour from the last 7 days of play."""

    __tablename__ = "game_rank"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    score: Mapped[float] = mapped_column(Float, index=True)
    visitors_7: Mapped[int] = mapped_column(Integer, default=0)  # different people who opened it in the last 7 days
    visitors_prev_7: Mapped[int] = mapped_column(Integer, default=0)  # ... in the 7 days before
    runs_7: Mapped[int] = mapped_column(Integer, default=0)
    players_7: Mapped[int] = mapped_column(Integer, default=0)  # different signed-in players who sent a score
    return_rate: Mapped[float] = mapped_column(Float, default=0)  # share of those players who came back on another day
    avg_session: Mapped[int | None] = mapped_column(Integer)  # seconds, only when the game uses the Kit
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FeaturedGame(Base):
    """A game an admin put on a homepage shelf: "featured" (has to be eligible) or "staff" (a staff pick)."""

    __tablename__ = "featured_games"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    shelf: Mapped[str] = mapped_column(String(10), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_by: Mapped[str] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GameDiscovery(Base):
    """What a game says about itself for discovery, from its startng.json: for now whether it works on a phone."""

    __tablename__ = "game_discovery"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    mobile: Mapped[bool] = mapped_column(Boolean, default=False)
