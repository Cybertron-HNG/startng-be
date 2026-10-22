"""Friends & follows (S2): who follows whom, and invite links that make two players friends.

Friends are not stored separately: two players are friends when each one follows the other.
Migrations: alembic/versions/0029_follows.py and 0030_follows_via_invite.py.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Follow(Base):
    """One player follows another (a player or a creator). One row per pair; you can't follow yourself."""
    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("follower_id", "target_user_id", name="uq_follows_pair"),
        CheckConstraint("follower_id <> target_user_id", name="ck_follows_not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    via_invite: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # made by accepting an invite link


class FriendInvite(Base):
    """A link (start.ng/f/<code>) that makes whoever accepts it friends with the inviter. Several people can
    accept the same link until it expires."""
    __tablename__ = "friend_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    inviter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
