from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...security import current_user
from . import service
from .schemas import AwardOut, ProgressOut, PublicProgressOut

# main.py mounts this with the feature's flag: every route answers 404 while "progress" is off.
router = APIRouter(tags=["progress"])


@router.get("/me/progress", response_model=ProgressOut)
def my_progress(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The signed-in player's runs, games, streaks, XP and level."""
    return asdict(service.snapshot(db, user.id))


@router.get("/me/awards", response_model=list[AwardOut])
def my_awards(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The items and badges the signed-in player owns, newest first."""
    return service.awards_of(db, user.id)


@router.get("/players/{user_id}/progress", response_model=PublicProgressOut)
def player_progress(user_id: int, db: Session = Depends(get_db)):
    """A player's public progress: level, streaks, games tried, tournament wins. 404 for an unknown player or one who
    keeps their page private."""
    snap = service.public_snapshot(db, user_id)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return asdict(snap)
