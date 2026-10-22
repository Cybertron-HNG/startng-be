from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Challenge(Base):
    """"Beat my score": a link to one of the player's runs (checked against the run log, never a score they type)."""

    __tablename__ = "challenges"
    __table_args__ = (Index("ix_challenges_challenger_game", "challenger_id", "game_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    score: Mapped[int] = mapped_column(BigInteger)  # the run's first stat
    stats: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    challenger: Mapped[User] = relationship()


class ChallengeAttempt(Base):
    """A friend who took the challenge: their best run for the game since they accepted."""

    __tablename__ = "challenge_attempts"

    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    best_score: Mapped[int | None] = mapped_column(BigInteger)
    best_stats: Mapped[dict | None] = mapped_column(JSON)
    beaten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
