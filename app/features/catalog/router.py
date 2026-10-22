from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...models import Game, User
from ...routers.games import _player_name
from ...security import current_user, optional_user
from . import service
from .models import GameRating

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["catalog"])

GAME = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")


class InfoOut(BaseModel):
    game: str
    category: str | None
    rating_count: int
    rating_avg: float | None  # null until the game has 5 ratings
    score: float  # smoothed average, for ordering


class CatalogOut(BaseModel):
    categories: list[str]
    games: list[InfoOut]  # games with a category or a rating; any other game is Uncategorized and unrated


class RatingOut(BaseModel):
    rating_count: int
    rating_avg: float | None
    mine: int | None  # the signed-in player's stars
    can_rate: bool
    why_not: str | None


class StarsIn(BaseModel):
    stars: int = Field(ge=1, le=5)


class CategoryIn(BaseModel):
    category: str | None = None  # null clears it


def _game_or_404(db: Session, name: str) -> Game:
    game = db.get(Game, name)
    if game is None or game.hidden is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no game with that name")
    return game


def _rating_out(db: Session, game: Game, user: User | None) -> RatingOut:
    i = service.info(db, game.name)
    mine = db.scalar(select(GameRating.stars).where(GameRating.user_id == user.id, GameRating.game_name == game.name)) if user else None
    ok, why = service.can_rate(db, user.id, game) if user else (False, "Sign in to rate games")
    return RatingOut(rating_count=i.rating_count, rating_avg=i.rating_avg, mine=mine, can_rate=ok, why_not=why)


@router.get("/catalog", response_model=CatalogOut)
def catalog(db: Session = Depends(get_db)):
    """Every game's category and rating, for the filters and cards on /games."""
    return CatalogOut(categories=service.CATEGORIES, games=[InfoOut(**i.__dict__) for i in service.all_info(db)])


@router.get("/games/{name}/rating", response_model=RatingOut)
def my_rating(name: str = GAME, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    return _rating_out(db, _game_or_404(db, name), user)


@router.put("/games/{name}/rating", response_model=RatingOut)
def rate(body: StarsIn, name: str = GAME, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Rate a game 1 to 5 stars (again to change it). Only after playing it for a minute."""
    try:
        service.rate(db, user.id, name, body.stars)
    except service.CatalogError as e:
        raise HTTPException(e.status, e.message)
    return _rating_out(db, _game_or_404(db, name), user)


@router.delete("/games/{name}/rating", response_model=RatingOut)
def unrate(name: str = GAME, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(GameRating).where(GameRating.user_id == user.id, GameRating.game_name == name))
    if row is not None:
        service.remove_rating(db, row)
    return _rating_out(db, _game_or_404(db, name), user)


@router.put("/games/{name}/category", response_model=InfoOut)
def set_category(body: CategoryIn, name: str = GAME, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner sets (or clears) the category in Your games. A publish whose startng.json has a category replaces it."""
    game = _game_or_404(db, name)
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the game's owner can change its category")
    try:
        service.set_category(db, name, body.category)
    except service.manifest.SchemaError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return InfoOut(**service.info(db, name).__dict__)


# ---- admin: take an abusive rating away

moderate = require("ratings.moderate")


class AdminRating(BaseModel):
    id: int
    game: str
    player: str
    stars: int
    at: datetime


@router.get("/admin/ratings", response_model=list[AdminRating])
def admin_ratings(game: str | None = Query(None, max_length=40), admin: AdminUser = Depends(moderate), db: Session = Depends(get_db)):
    """The latest 100 ratings (of one game, if named)."""
    q = select(GameRating).order_by(GameRating.updated_at.desc()).limit(100)
    if game:
        q = q.where(GameRating.game_name == game)
    return [AdminRating(id=r.id, game=r.game_name, player=_player_name(r.user), stars=r.stars, at=r.updated_at) for r in db.scalars(q).all()]


@router.delete("/admin/ratings/{rating_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_remove_rating(rating_id: int, admin: AdminUser = Depends(moderate), db: Session = Depends(get_db)):
    row = db.get(GameRating, rating_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No rating with that id")
    adm.log(db, admin, "rating.remove", row.game_name, stars=row.stars, user_id=row.user_id)
    service.remove_rating(db, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
