"""The leagues' own tables. Migration: alembic/versions/0039_leagues.py."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LeagueRound(Base):
    """One round of one track. Unique per track and start, so two requests can't make the same round."""

    __tablename__ = "league_rounds"
    __table_args__ = (UniqueConstraint("track", "starts_at", name="uq_league_rounds_track_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    track: Mapped[str] = mapped_column(String(16))  # "sprint", "daily" or "weekly"
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # set once, when the round is settled


class LeagueMember(Base):
    """A player's tier in one track. Made on their first run; kept between rounds."""

    __tablename__ = "league_members"
    __table_args__ = (UniqueConstraint("user_id", "track", name="uq_league_members_user_track"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    track: Mapped[str] = mapped_column(String(16))
    tier: Mapped[int] = mapped_column(Integer, default=0)  # index into service.TIERS (0 = Bronze)
    last_round_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the last round they played
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LeagueGroup(Base):
    """Up to service.GROUP_SIZE players of one tier in one round, filled in the order they arrive."""

    __tablename__ = "league_groups"
    __table_args__ = (UniqueConstraint("round_id", "tier", "number", name="uq_league_groups_round_tier_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("league_rounds.id", ondelete="CASCADE"), index=True)
    tier: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, ... within the tier


class LeagueEntry(Base):
    """A player in a round. Unique per round and player, and per group and seat, so a full group can't take a 31st."""

    __tablename__ = "league_entries"
    __table_args__ = (
        UniqueConstraint("round_id", "user_id", name="uq_league_entries_round_user"),
        UniqueConstraint("group_id", "seat", name="uq_league_entries_group_seat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("league_rounds.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("league_groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    seat: Mapped[int] = mapped_column(Integer)  # 1 to service.GROUP_SIZE
    tier: Mapped[int] = mapped_column(Integer)  # their tier during this round
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Filled when the round is settled:
    points: Mapped[int | None] = mapped_column(Integer)
    place: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(String(8))  # "up", "down", "stay" or "out" (no counted runs)
