from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...security import optional_user
from ..tags.service import TagError
from . import service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["surprise"])


class SurpriseOut(BaseModel):
    game: str  # its name; its page is /games/<name>


@router.get("/discover/surprise", response_model=SurpriseOut)
def surprise(tag: str | None = Query(None, max_length=60), exclude: str | None = Query(None, max_length=40),
             user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """A random live game (Ziki #18). tag: only from that category. exclude: not this one (the game you're on)."""
    try:
        name = service.pick(db, user.id if user else None, tag, exclude)
    except TagError as e:
        raise HTTPException(e.status, e.message)
    if name is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No games to pick from")
    return SurpriseOut(game=name)
