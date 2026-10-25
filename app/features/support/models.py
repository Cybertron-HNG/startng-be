from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProblemReport(Base):
    """A signed-in player's report of a problem with start.ng itself (not with one game: that's a game report).

    status: "new" (waiting for an admin), "confirmed" (a real bug: the reporter is paid), "duplicate" (already
    reported: a thank-you, no coins), "not_a_bug", "fixed" (a confirmed bug that's now fixed)."""

    __tablename__ = "problem_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    what: Mapped[str] = mapped_column(Text)  # what happened, in the player's words
    page: Mapped[str | None] = mapped_column(String(300))  # the page they were on (a path, no query string)
    browser: Mapped[str | None] = mapped_column(String(120))  # e.g. "Chrome 130 on Windows", never more
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)
    similar: Mapped[list | None] = mapped_column(JSON)  # ids of earlier reports that look like this one
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("problem_reports.id", ondelete="SET NULL"))
    admin_note: Mapped[str | None] = mapped_column(Text)  # what admins tell the reporter
    coins: Mapped[int] = mapped_column(Integer, default=0)  # what the reporter was paid for it
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    user: Mapped[User] = relationship()
