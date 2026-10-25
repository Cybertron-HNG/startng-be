"""Private tournaments' own tables. Migration: alembic/versions/0019_private_tournaments.py."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import Game, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PrivateTournament(Base):
    __tablename__ = "private_tournaments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # the invite link: start.ng/t/<code>
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(60))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # F9: made by an admin on the admin page, to share in a group. Only people who register (join) see its games, and
    # it can start later. Its maker isn't a member. max_members: None means MAX_MEMBERS.
    by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    max_members: Mapped[int | None] = mapped_column(Integer)

    owner: Mapped[User] = relationship()
    games: Mapped[list["PrivateTournamentGame"]] = relationship(order_by="PrivateTournamentGame.position", cascade="all, delete-orphan")
    members: Mapped[list["PrivateTournamentMember"]] = relationship(cascade="all, delete-orphan")


class PrivateTournamentGame(Base):
    __tablename__ = "private_tournament_games"
    __table_args__ = (UniqueConstraint("tournament_id", "game_name", name="uq_private_tournament_games"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("private_tournaments.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)

    game: Mapped[Game] = relationship()


class PrivateTournamentMember(Base):
    __tablename__ = "private_tournament_members"
    __table_args__ = (UniqueConstraint("tournament_id", "user_id", name="uq_private_tournament_members"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("private_tournaments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship()


class PrivateTournamentScore(Base):
    """A member's best run in one of the tournament's games, inside its window."""

    __tablename__ = "private_tournament_scores"
    __table_args__ = (UniqueConstraint("tournament_id", "game_name", "user_id", name="uq_private_tournament_scores"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("private_tournaments.id", ondelete="CASCADE"), index=True)
    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    best: Mapped[int] = mapped_column(BigInteger)
    stats: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    user: Mapped[User] = relationship()
