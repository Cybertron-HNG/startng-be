from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Game
from ...routers.games import _player_name
from ...routers.pages import _title
from . import service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["remix"])


class GameRef(BaseModel):
    name: str
    title: str
    by: str


class RemixOut(BaseModel):
    allowed: bool
    fork_url: str | None
    remix_of: GameRef | None
    remixes: list[GameRef]
    count: int


def ref(game: Game) -> GameRef:
    return GameRef(name=game.name, title=_title(game.name), by=_player_name(game.owner, "a builder"))


@router.get("/games/{name}/remix", response_model=RemixOut)
def game_remix(response: Response, name: str = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"), db: Session = Depends(get_db)):
    """A game's remix facts, for its page: the Remix it button, where it came from, and its remixes. Public."""
    game = db.get(Game, name)
    if game is None or game.hidden:
        raise HTTPException(404, "Game not found")
    found = service.info(db, game)
    response.headers["Cache-Control"] = "public, max-age=60"
    return RemixOut(
        allowed=found.allowed,
        fork_url=found.fork_url,
        remix_of=ref(found.remix_of) if found.remix_of else None,
        remixes=[ref(g) for g in found.remixes],
        count=found.count,
    )
