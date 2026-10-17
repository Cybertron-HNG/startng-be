"""Favorites: the games a signed-in player saved, kept on their account so they follow them to
every device.

A favorite is a key: "g:<name>" for a game on start.ng, "i:<id>" for a game by an independent
creator (the same keys the website uses). Newest first.
"""
import re

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ApiError
from ..models import Favorite, User
from ..security import current_user

router = APIRouter(prefix="/favorites", tags=["favorites"])

KEY = re.compile(r"^(g:[a-z0-9][a-z0-9-]{0,39}|i:[0-9]{1,9})$")
MAX_FAVORITES = 500


class FavoritesSync(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=MAX_FAVORITES)


def _key(key: str) -> str:
    if not KEY.match(key):
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad_favorite", "That isn't a game you can favorite")
    return key


def _keys(db: Session, user: User) -> list[str]:
    rows = db.scalars(select(Favorite.key).where(Favorite.user_id == user.id).order_by(Favorite.created_at.desc(), Favorite.id.desc()))
    return list(rows)


def _add(db: Session, user: User, keys: list[str]) -> None:
    have = set(db.scalars(select(Favorite.key).where(Favorite.user_id == user.id)))
    new = [k for k in dict.fromkeys(keys) if k not in have]
    if len(have) + len(new) > MAX_FAVORITES:
        raise ApiError(status.HTTP_409_CONFLICT, "too_many_favorites", f"You can save up to {MAX_FAVORITES} favorites")
    # Added oldest first, so the first key in the list ends up newest.
    db.add_all(Favorite(user_id=user.id, key=k) for k in reversed(new))
    db.commit()


@router.get("", response_model=list[str])
def list_favorites(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _keys(db, user)


@router.post("/sync", response_model=list[str])
def sync_favorites(body: FavoritesSync, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Adds the favorites a device saved before it was signed in, and returns the full list."""
    _add(db, user, [_key(k) for k in body.keys])
    return _keys(db, user)


@router.put("/{key}", response_model=list[str])
def add_favorite(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _add(db, user, [_key(key)])
    return _keys(db, user)


@router.delete("/{key}", response_model=list[str])
def remove_favorite(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(Favorite).where(Favorite.user_id == user.id, Favorite.key == _key(key)))
    db.commit()
    return _keys(db, user)
