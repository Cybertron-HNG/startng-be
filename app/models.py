from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Their handle, lowercase and unique (app/usernames.py): the address of their page, start.ng/p/<username>.
    username: Mapped[str | None] = mapped_column(String(24), unique=True, index=True)
    # The player's character, shared with every game: {"name": ..., "style": {...}}
    character: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # When they last used start.ng (updated at most every few minutes). Picks who gets a bench
    # in the Hangout.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # Set when an admin blocks them (app/admin.py): they can't sign in or publish, and their games are hidden.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # Whether the live version meets the game standards (app/checks.standards), checked on every publish and
    # update. None = published before the standards existed; such games still take part in tournaments for now.
    meets_standards: Mapped[bool | None] = mapped_column(Boolean)
    # Whether the live version loads the start.ng Kit (https://start.ng/kit.js, PRD M1), set with meets_standards.
    uses_kit: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Hidden from start.ng while admins look at it (app/admin.py): "reported" (a player reported it), "flagged"
    # (an admin flagged it) or "blocked" (its developer is blocked). None = listed as usual.
    hidden: Mapped[str | None] = mapped_column(String(20), index=True)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


# ---- Daily tournaments (app/tournaments.py): a few random games, 24 hours, points for the best players


class Tournament(Base):
    """One day's tournament. Its own start and end, so a future admin page can move or change one."""

    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Set once the results are worked out (points given and the winner named).
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    games: Mapped[list["TournamentGame"]] = relationship(order_by="TournamentGame.position", cascade="all, delete-orphan")
    winner: Mapped[User | None] = relationship()


class TournamentGame(Base):
    __tablename__ = "tournament_games"
    __table_args__ = (UniqueConstraint("tournament_id", "game_name", name="uq_tournament_games"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)

    game: Mapped[Game] = relationship()


class TournamentEntry(Base):
    """A player who entered (by sharing the tournament). Only entered players' scores count."""

    __tablename__ = "tournament_entries"
    __table_args__ = (UniqueConstraint("tournament_id", "user_id", name="uq_tournament_entries"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TournamentScore(Base):
    """A player's best run in one tournament game, inside the tournament's 24 hours."""

    __tablename__ = "tournament_scores"
    __table_args__ = (UniqueConstraint("tournament_id", "game_name", "user_id", name="uq_tournament_scores"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    best: Mapped[int] = mapped_column(BigInteger)
    stats: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship()


class TournamentPoints(Base):
    """What a player earned in a finished tournament. Summed for the overall leaderboard."""

    __tablename__ = "tournament_points"
    __table_args__ = (UniqueConstraint("tournament_id", "user_id", name="uq_tournament_points"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    points: Mapped[int] = mapped_column(Integer)
    firsts: Mapped[int] = mapped_column(Integer)  # games they came first in
    place: Mapped[int] = mapped_column(Integer)  # their place in the day's standings

    user: Mapped[User] = relationship()


# ---- Admin (app/admin.py): roles, admins, reports, contacting developers and the activity log


class Role(Base):
    """A named set of admin permissions (app/admin.PERMISSIONS), made by a super admin."""

    __tablename__ = "admin_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Admin(Base):
    """Someone a super admin added by email. There's no admin sign-up: they sign in with Google as that email
    and get their role's permissions. Super admins come from settings.super_admin_emails, not this table."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("admin_roles.id", ondelete="SET NULL"))
    added_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    role: Mapped[Role | None] = relationship()


class GameReport(Base):
    """A report on a game: from a signed-in player, or a flag from an admin (by_admin). A player's report waits
    ("open") until an admin approves it, which hides the game, or dismisses it. An admin's flag is approved at once."""

    __tablename__ = "game_reports"
    __table_args__ = (Index("ix_game_reports_game_status", "game_name", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(20))
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)  # what the admin decided

    game: Mapped[Game] = relationship()
    reporter: Mapped[User | None] = relationship()


class DeveloperContact(Base):
    """An admin reached out to a game's developer about an issue. After settings.contacts_before_block
    different issues, admins can block or delete the developer."""

    __tablename__ = "developer_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    developer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str | None] = mapped_column(String(40))  # kept as text: the game may be deleted later
    report_id: Mapped[int | None] = mapped_column(ForeignKey("game_reports.id", ondelete="SET NULL"))
    issue: Mapped[str] = mapped_column(Text)
    admin_email: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Activity(Base):
    """Everything that happens on start.ng, for the admin page's activity log (app/activity.record): accounts made,
    games published and updated, reports, tournaments, and what admins do. kind says who did it: "player",
    "developer", "admin" or "system" (start.ng itself, e.g. an auto-update)."""

    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(255))  # their email; None for start.ng itself
    kind: Mapped[str] = mapped_column(String(12), index=True)
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class FeatureSwitch(Base):
    """A feature an admin switched on or off in the admin page. It wins over the FEATURES setting; deleting the row goes back
    to the setting (app/flags.py)."""

    __tablename__ = "feature_switches"

    feature: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    changed_by: Mapped[str] = mapped_column(String(255))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
