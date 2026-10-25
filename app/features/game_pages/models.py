from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GamePage(Base):
    """One page about a game. "draft" is waiting for a person, "approved" is public, "skipped" was not made (see reason)."""

    __tablename__ = "game_pages"
    __table_args__ = (UniqueConstraint("game_name", "kind", name="uq_game_pages"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # about | how-to-play | faq | walkthrough
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)  # draft | approved | skipped
    title: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(String(300), default="")  # the meta description
    body: Mapped[str] = mapped_column(Text, default="")  # a small markdown: ## headings, paragraphs, - bullets, 1. steps
    reason: Mapped[str | None] = mapped_column(String(200))  # why it was skipped
    facts: Mapped[dict | None] = mapped_column(JSON)  # what it was written from, so a person can check
    edited: Mapped[bool] = mapped_column(default=False)  # a person changed the text: do not write over it
    writer: Mapped[str] = mapped_column(String(10), default="rules")  # who wrote the text: "rules" (the templates) or "agent" (the daily agent, app/features/game_pages/agent.py)
    held: Mapped[bool] = mapped_column(default=False)  # a person took the page down: it is never approved by itself again
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
