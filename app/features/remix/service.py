"""What other features may call: a game's remix facts. Also owns the startng.json "remix" and "remix_of" sections."""
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import manifest
from ...models import Game
from ...publish import PublishError, parse_repo_url
from .models import GameRemix

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # a game's name, as in its address <name>.start.ng
SHOWN_REMIXES = 12  # remixes listed on the original's page (the count covers all of them); twin in startng-fe src/features/remix


# ---- startng.json "remix": true and "remix_of": "<game>"

def parse_remix(value) -> bool:
    if not isinstance(value, bool):
        raise manifest.SchemaError('remix must be true or false ("remix": true lets other creators remix your game)')
    return value


def parse_remix_of(value) -> str | None:
    if value is None:
        return None  # "remix_of": null clears it
    if not isinstance(value, str) or not NAME_RE.match(value.strip()):
        raise manifest.SchemaError('remix_of must be the name of the start.ng game you remixed, like "remix_of": "coin-rush"')
    return value.strip()


def _row(db: Session, game_name: str) -> GameRemix:
    row = db.get(GameRemix, game_name)
    if row is None:
        row = GameRemix(game_name=game_name, allowed=False)
        db.add(row)
    return row


def save_remix(db: Session, game, value: bool) -> None:
    _row(db, game.name).allowed = value


def save_remix_of(db: Session, game, value: str | None) -> None:
    _row(db, game.name).remix_of = None if value == game.name else value  # a game can't be its own remix


manifest.register_section("remix", parse_remix, save_remix)
manifest.register_section("remix_of", parse_remix_of, save_remix_of)


# ---- reading

@dataclass
class RemixInfo:
    allowed: bool = False
    fork_url: str | None = None  # GitHub's fork page for the game's repo, only while remixing is allowed
    remix_of: Game | None = None  # the original, when it is still listed
    remixes: list[Game] = field(default_factory=list)  # the newest SHOWN_REMIXES that are listed
    count: int = 0  # every listed remix


def fork_url(repo_url: str) -> str | None:
    try:
        return parse_repo_url(repo_url).url + "/fork"
    except PublishError:
        return None


def info(db: Session, game: Game) -> RemixInfo:
    row = db.get(GameRemix, game.name)
    out = RemixInfo()
    if row is not None:
        out.allowed = bool(row.allowed)
        if row.allowed:
            out.fork_url = fork_url(game.repo_url)
        if row.remix_of:
            original = db.get(Game, row.remix_of)
            out.remix_of = original if original is not None and not original.hidden else None
    listed = (
        select(Game)
        .join(GameRemix, GameRemix.game_name == Game.name)
        .where(GameRemix.remix_of == game.name, Game.hidden.is_(None), Game.name != game.name)
    )
    out.count = db.scalar(select(func.count()).select_from(listed.subquery())) or 0
    out.remixes = list(db.scalars(listed.order_by(Game.created_at.desc()).limit(SHOWN_REMIXES)).all())
    return out
