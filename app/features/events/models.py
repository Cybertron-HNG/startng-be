"""The events feature's own tables. Migration: alembic/versions/0052_events.py."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Event(Base):
    """One event. spotlight is true for at most one event (a unique index on the true rows enforces it)."""

    __tablename__ = "events"
    __table_args__ = (Index("uq_events_one_spotlight", "spotlight", unique=True, postgresql_where=text("spotlight"), sqlite_where=text("spotlight")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(80))
    tagline: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    venue: Mapped[str] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(200))
    ticket_url: Mapped[str | None] = mapped_column(String(300))
    ticket_label: Mapped[str] = mapped_column(String(30), default="Get tickets")
    website_url: Mapped[str | None] = mapped_column(String(300))
    highlights: Mapped[list] = mapped_column(JSON, default=list)  # [{"title": ..., "text": ...}]
    partners: Mapped[list] = mapped_column(JSON, default=list)  # [{"name": ..., "url": ... or None}]
    spotlight: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EventImage(Base):
    """A picture of an event, saved again as WebP. The first one (lowest position) is the cover."""

    __tablename__ = "event_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    alt: Mapped[str | None] = mapped_column(String(160))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
