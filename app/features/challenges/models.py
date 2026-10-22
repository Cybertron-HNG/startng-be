"""Challenge links' own tables (migration 0030_challenges)."""
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import Game, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Challenge(Base):
    """A player's run, shared as a target: start.ng/c/<code>. The score is copied from the run when it's made, so the
    challenge outlives the run log's retention and can't be changed by anything sent later."""

    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the run it was made from (runs are purged; no FK)
    score: Mapped[int] = mapped_column(BigInteger)  # the run's first stat: what a friend has to beat
    stats: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    challenger: Mapped[User] = relationship()
    game: Mapped[Game] = relationship()


class ChallengeAttempt(Base):
    """A friend who took a challenge on (pressed Play on its page). Only their runs after that count."""

    __tablename__ = "challenge_attempts"
    __table_args__ = (UniqueConstraint("challenge_id", "user_id", name="uq_challenge_attempts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    best: Mapped[int | None] = mapped_column(BigInteger)  # their best run's first stat since accepting
    beaten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    challenge: Mapped[Challenge] = relationship()
    user: Mapped[User] = relationship()
