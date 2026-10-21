from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...security import current_user
from . import service
from .schemas import AwardOut, ProgressOut

# main.py mounts this with the feature's flag: every route answers 404 while "progress" is off.
router = APIRouter(prefix="/me", tags=["progress"])


@router.get("/progress", response_model=ProgressOut)
def my_progress(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The signed-in player's runs, games, streaks, XP and level."""
    return asdict(service.snapshot(db, user.id))


@router.get("/awards", response_model=list[AwardOut])
def my_awards(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The items and badges the signed-in player owns, newest first."""
    return service.awards_of(db, user.id)
