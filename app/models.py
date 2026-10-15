from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    # The player's character, shared with every game: {"name": ..., "style": {...}}
    character: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # When they last used start.ng (updated at most every few minutes). Picks who gets a bench
    # in the Hangout.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Game(Base):
    """A published game. The name is its bucket folder and its address: <name>.start.ng."""

    __tablename__ = "games"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    repo_url: Mapped[str] = mapped_column(String(300))
    branch: Mapped[str | None] = mapped_column(String(100))
    # The card picture: a path in the game's folder (app.covers), and when it last changed.
    cover: Mapped[str | None] = mapped_column(String(200))
    cover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    owner: Mapped[User] = relationship()


class Score(Base):
    """A player's best score in a game: one row per player per game."""

    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("user_id", "game_name", name="uq_scores_user_game"),
        Index("ix_scores_game_best", "game_name", "best"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    best: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship()


class PendingGame(Base):
    """A game sent to POST /games/publish without signing in, waiting to be approved.

    The reply gave the publisher a link, <site_url>/approve/<code>. Whoever opens it signs in with
    start.ng and approves, and the game goes live on their account; from then on that repo
    updates straight away. Unapproved ones are removed when they expire.
    """

    __tablename__ = "pending_games"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    repo_url: Mapped[str] = mapped_column(String(300), index=True)
    branch: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
