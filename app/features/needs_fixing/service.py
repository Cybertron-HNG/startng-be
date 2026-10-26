"""What other features use to mark a game as needing a fix (game health does, when a game keeps failing)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...models import Game
from .models import FixRequest

REASON = "needs_fixing"  # games.hidden while it waits
HEALTH_CHECK = "start.ng health check"  # created_by when game health marked it (F8)
adm.register_hide_reason(REASON)


def open_request(db: Session, game_name: str) -> FixRequest | None:
    return db.scalar(select(FixRequest).where(FixRequest.game_name == game_name, FixRequest.status != "closed"))


def mark(db: Session, store, game: Game, note: str, by: str) -> FixRequest | None:
    """Hide a game until its owner fixes it and an admin puts it back. by: the admin's email, or what marked it
    ("start.ng health check"). None when the game already has an open request. Commits."""
    if open_request(db, game.name):
        return None
    r = FixRequest(game_name=game.name, note=note.strip(), created_by=by)
    db.add(r)
    adm.hide_game(db, store, game, REASON)  # commits
    db.refresh(r)
    return r
