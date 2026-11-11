"""This feature's table. Migration: alembic/versions/0050_phone_checks.py (renumber right before merging)."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...db import Base
from ...models import Game


class PhoneCheck(Base):
    """The latest phone check of a game.

    status: "unchecked" (new, or just updated: the background job checks it soon), "ok", "worth_a_look" (findings to
    show its owner) or "error" (it couldn't be checked, e.g. index.html is missing: game health reports that).
    method: "browser" (opened at phone widths) or "files" (read its files only)."""

    __tablename__ = "phone_checks"

    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="unchecked", index=True)
    method: Mapped[str | None] = mapped_column(String(8))
    findings: Mapped[list | None] = mapped_column(JSON)  # [{"code", "message"}]
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[Game] = relationship()
