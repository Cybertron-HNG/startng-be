from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
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
    # Keeping it up to date (app/auto_update.py): the commit that's live, GitHub's fingerprint of
    # that answer (so unchanged repos cost nothing to check), and when it was last checked.
    last_commit: Mapped[str | None] = mapped_column(String(40))
    commit_etag: Mapped[str | None] = mapped_column(String(200))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the latest push couldn't go live ({commit, at, detail, errors}); cleared by the
    # next successful publish. Shown to the owner in Your games.
    update_problem: Mapped[dict | None] = mapped_column(JSON)
    # The game's own leaderboard stats from its startng.json (app/leaderboard.py); None = plain score
    leaderboard: Mapped[list | None] = mapped_column(JSON)
    # One line about the game from its startng.json, shown on its page at start.ng/games/<name> (None: the page writes one)
    description: Mapped[str | None] = mapped_column(String(300))
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
    best: Mapped[int] = mapped_column(BigInteger)  # the first stat of the best run, for quick sorting
    stats: Mapped[dict | None] = mapped_column(JSON)  # the best run's stats; None = a plain score
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


class IndieGame(Base):
    """Indie Spotlight: a game an independent creator made and hosts somewhere else.

    start.ng only lists it: its name, a link to play it, a short description and a cover. The
    cover is stored here (a small card-sized WebP made by app.covers) and served by the API.
    """

    __tablename__ = "indie_games"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    url: Mapped[str] = mapped_column(String(300), unique=True)
    description: Mapped[str] = mapped_column(Text)
    cover: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    owner: Mapped[User] = relationship()


class GamePlay(Base):
    """Someone opened a game from start.ng. Counted to show the most played games on the homepage.

    visitor is a one-way fingerprint (never the address itself), so the same person opening the
    same game again within a short while counts once. Plays older than PLAYS_KEPT_DAYS are removed.
    """

    __tablename__ = "game_plays"
    __table_args__ = (Index("ix_game_plays_game_at", "game_name", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    visitor: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class IndiePlay(Base):
    """Someone opened a game by an independent creator from start.ng (see GamePlay)."""

    __tablename__ = "indie_plays"
    __table_args__ = (Index("ix_indie_plays_game_at", "indie_id", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    indie_id: Mapped[int] = mapped_column(ForeignKey("indie_games.id", ondelete="CASCADE"))
    visitor: Mapped[str] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Favorite(Base):
    """A game a player saved. The key says which one: "g:<name>" for a game on start.ng, "i:<id>"
    for a game by an independent creator. Kept as a key (not a foreign key) so a favorite saved
    on a device before sign-in can be sent over even if the game has since gone."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_favorites_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
