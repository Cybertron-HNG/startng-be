from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Subscriber(Base):
    """An email address start.ng may write to: a signed-in player's (confirmed by Google) or one left by a visitor
    (confirmed by clicking the link we send: double opt-in). What they get is in subscriber_topics."""

    __tablename__ = "email_subscribers"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirm_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    confirm_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_topics: Mapped[list | None] = mapped_column(JSON)  # topics a visitor asked for, added once they confirm
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True)  # in every email's unsubscribe link
    # A hard bounce, spam complaint or blocked address (from Brevo's webhook): never written to again.
    bad_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bad_reason: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    topics: Mapped[list["SubscriberTopic"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def topic_names(self) -> list[str]:
        return sorted(t.topic for t in self.topics)


class SubscriberTopic(Base):
    __tablename__ = "email_subscriber_topics"

    subscriber_id: Mapped[int] = mapped_column(ForeignKey("email_subscribers.id", ondelete="CASCADE"), primary_key=True)
    topic: Mapped[str] = mapped_column(String(20), primary_key=True)


class Email(Base):
    """One email, from queued to sent. notify() adds a note to the player's queued email for the day when there is
    one (so they get one email a day), and the sender renders the notes when it sends.

    status: "queued", "sent", "failed" (gave up after retries), "skipped" (unsubscribed, bad address, or not in
    EMAIL_ONLY_TO), "capped" (they already had today's email), "bounced" (Brevo said so after sending)."""

    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("email_subscribers.id", ondelete="CASCADE"), index=True)
    to_email: Mapped[str] = mapped_column(String(255))
    day: Mapped[str] = mapped_column(String(10), index=True)  # the player's day (start.ng's time zone), for the cap
    capped: Mapped[bool] = mapped_column(default=True)  # counts toward the 1-a-day cap (not confirmations or tests)
    notes: Mapped[list] = mapped_column(JSON)  # [{"kind": ..., "data": {...}}], rendered when it's sent
    subject: Mapped[str | None] = mapped_column(String(200))  # what was sent
    status: Mapped[str] = mapped_column(String(10), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    send_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_id: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subscriber: Mapped[Subscriber] = relationship()
