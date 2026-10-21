"""Crews' own tables. Migration: alembic/versions/0020_crews.py."""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Crew(Base):
    __tablename__ = "crews"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # the invite link: start.ng/join/<code>
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(12))  # family, school, class, city or friends
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    owner: Mapped[User] = relationship()
    members: Mapped[list["CrewMember"]] = relationship(cascade="all, delete-orphan", order_by="CrewMember.joined_at")


class CrewMember(Base):
    __tablename__ = "crew_members"
    __table_args__ = (UniqueConstraint("crew_id", "user_id", name="uq_crew_members"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    crew_id: Mapped[int] = mapped_column(ForeignKey("crews.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship()
