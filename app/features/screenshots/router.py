from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Game
from . import service
from .schemas import ScreenshotsOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["screenshots"])


@router.get("/games/{name}/screenshots", response_model=ScreenshotsOut)
def game_screenshots(name: str = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"), db: Session = Depends(get_db)):
    """A game's screenshots for its page, in the order its startng.json lists them ([] when it lists none)."""
    game = db.get(Game, name)
    if game is None or game.hidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no game with that name on start.ng")
    return ScreenshotsOut(screenshots=service.screenshots_of(db, name))
