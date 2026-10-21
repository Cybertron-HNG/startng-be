from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Fingerprint(Base):
    """What a live game looks like, to spot copies: hashes of its page, its cover and its file list, and its name
    with versions and copies taken off. Saved when it's published or updated (and by an admin's scan)."""

    __tablename__ = "game_fingerprints"

    game_name: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), primary_key=True)
    index_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    cover_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    files_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    name_key: Mapped[str] = mapped_column(String(40), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Decision(Base):
    """What an admin decided about a pair of look-alike games: "keep" (both are fine) or "hidden" (target was
    hidden as a copy of the other). game_a < game_b, so a pair is stored once."""

    __tablename__ = "duplicate_decisions"
    __table_args__ = (UniqueConstraint("game_a", "game_b", name="uq_duplicate_decisions"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_a: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), index=True)
    game_b: Mapped[str] = mapped_column(ForeignKey("games.name", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(8))
    target: Mapped[str | None] = mapped_column(String(40))  # the game hidden, for "hidden"
    decided_by: Mapped[str] = mapped_column(String(255))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
