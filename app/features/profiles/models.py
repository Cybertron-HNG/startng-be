from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProfileSettings(Base):
    """A player's choices about their page. No row means the defaults: public, showing favorites and ranks."""

    __tablename__ = "profile_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    public: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    show_favorites: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    show_ranks: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    notice_seen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # the first-time "your page is public" notice
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
