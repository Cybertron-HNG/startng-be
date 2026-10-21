from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


class ModerationWord(Base):
    """A word admins added: blocked (kind "block"; strong = caught inside other words too) or allowed ("allow"),
    which overrides the default list for a word that's fine on start.ng."""

    __tablename__ = "moderation_words"

    id: Mapped[int] = mapped_column(primary_key=True)
    word: Mapped[str] = mapped_column(String(40), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # "block" or "allow"
    strong: Mapped[bool] = mapped_column(Boolean, default=False)
    added_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
