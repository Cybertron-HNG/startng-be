"""The tags and which games have them."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)  # the page address: /categories/<slug>
    label: Mapped[str] = mapped_column(String(60))
    group_name: Mapped[str] = mapped_column(String(40), index=True)
    blurb: Mapped[str] = mapped_column(String(160), default="")
    position: Mapped[int] = mapped_column(default=0)  # the order inside its group
    everywhere: Mapped[bool] = mapped_column(Boolean, default=False)  # on every game, e.g. "Web games"


class GameTag(Base):
    """A tag on a game. "suggested" is the rules' guess; only "approved" is shown to players."""

    __tablename__ = "game_tags"
    __table_args__ = (UniqueConstraint("game_name", "tag_id", name="uq_game_tags"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="suggested", index=True)  # suggested | approved | rejected (a suggestion an admin turned down: it is not suggested again)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # who decided: "rules" (suggested or approved by the rules), "creator" (the game's own startng.json), "review" (start.ng's own
    # review of the games that were live first), "admin" (an admin in the tags tab). Rules never touch a game someone else decided for.
    source: Mapped[str] = mapped_column(String(10), default="rules")


class IndieGameTag(Base):
    """A category an independent creator picked for their game. Shown only on the game's own page: independent games are not
    on the category pages or the home page."""

    __tablename__ = "indie_game_tags"
    __table_args__ = (UniqueConstraint("indie_id", "tag_id", name="uq_indie_game_tags"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    indie_id: Mapped[int] = mapped_column(ForeignKey("indie_games.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)


class IndieTagRequest(Base):
    """The category an independent creator wrote under "other" because none of ours fit. Read by an admin; never shown to players."""

    __tablename__ = "indie_tag_requests"
    __table_args__ = (UniqueConstraint("indie_id", "text", name="uq_indie_tag_requests"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    indie_id: Mapped[int] = mapped_column(ForeignKey("indie_games.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TagRequest(Base):
    """A category a creator wrote under "other" in startng.json because none of ours fit. Not shown to players: an admin reads them
    and adds a tag to the list when many games ask for the same thing."""

    __tablename__ = "tag_requests"
    __table_args__ = (UniqueConstraint("game_name", "text", name="uq_tag_requests"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(String(40), ForeignKey("games.name", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
