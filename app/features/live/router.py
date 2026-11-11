from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from . import service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["live"])


class MomentOut(BaseModel):
    kind: str  # "record": `who` took #1 on `game`
    game: str
    title: str
    who: str  # "Someone" when the player's profile is private
    text: str
    at: datetime


class LiveOut(BaseModel):
    playing: dict[str, int]  # game -> players now, only games with 2 or more
    moments: list[MomentOut]  # newest first, the last 24 hours
    tournament_playing: int  # players on today's tournament games now (0 under 2)


@router.get("/live", response_model=LiveOut)
def live(response: Response, db: Session = Depends(get_db)):
    """Real signs of play right now (Ziki #37). Public; the same for everyone, so it caches for a few seconds."""
    response.headers["Cache-Control"] = "public, max-age=15"
    return service.snapshot(db)
