from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...models import Game, User
from ...routers.pages import _title
from ...security import current_user, optional_user
from . import service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["discovery"])

GAME = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")


class ShelvesOut(BaseModel):
    featured: list[str]
    staff: list[str]
    new: list[str]
    most_played: list[str]
    most_improved: list[str]
    continue_playing: list[str]  # empty unless signed in


class NextOut(BaseModel):
    games: list[str]
    tournament: str | None = None  # the tournament these come from, when the game is in one that is on now


class CheckOut(BaseModel):
    key: str
    label: str
    ok: bool
    required: bool


class FeaturingOut(BaseModel):
    featured: bool
    staff_pick: bool
    eligible: bool
    checks: list[CheckOut]


@router.get("/discover/shelves", response_model=ShelvesOut)
def shelves(user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """The homepage shelves, as game names (the website has the games)."""
    return service.shelves(db, user.id if user else None)


@router.get("/discover/next", response_model=NextOut)
def play_next(after: str = Query(..., max_length=40), limit: int = Query(3, ge=1, le=3), user: User | None = Depends(optional_user),
              db: Session = Depends(get_db)):
    """One to three games to play after this one (contract C10)."""
    games, tournament = service.play_next_with_context(db, after, user.id if user else None, limit)
    return NextOut(games=games, tournament=tournament)


def _featuring(db: Session, game: Game) -> FeaturingOut:
    checks = service.eligibility(db, game)
    return FeaturingOut(featured=game.name in service.on_shelf(db, "featured"), staff_pick=game.name in service.on_shelf(db, "staff"),
                        eligible=all(c.ok for c in checks if c.required), checks=[CheckOut(**c.__dict__) for c in checks])


@router.get("/games/{name}/featuring", response_model=FeaturingOut)
def my_featuring(name: str = GAME, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner's checklist: what a game needs to be featured, and what it is missing."""
    game = db.get(Game, name)
    if game is None or game.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game of yours with that name")
    return _featuring(db, game)


# ---- admin: the Featured tab

edit = require("featured.edit")


class AdminGame(BaseModel):
    game: str
    title: str
    eligible: bool
    missing: list[str]


class AdminFeatured(BaseModel):
    featured: list[AdminGame]
    staff: list[AdminGame]
    candidates: list[AdminGame]  # every other visible game, so an admin can add one


def _admin_game(db: Session, game: Game) -> AdminGame:
    checks = service.eligibility(db, game)
    missing = [c.label for c in checks if c.required and not c.ok]
    return AdminGame(game=game.name, title=_title(game.name), eligible=not missing, missing=missing)


@router.get("/admin/featured", response_model=AdminFeatured)
def admin_featured(admin: AdminUser = Depends(edit), db: Session = Depends(get_db)):
    games = {g.name: g for g in service.visible(db)}
    featured, staff = service.on_shelf(db, "featured"), service.on_shelf(db, "staff")
    return AdminFeatured(
        featured=[_admin_game(db, games[n]) for n in featured if n in games], staff=[_admin_game(db, games[n]) for n in staff if n in games],
        candidates=[_admin_game(db, g) for n, g in sorted(games.items()) if n not in featured])


@router.put("/admin/featured/{shelf}/{name}", status_code=status.HTTP_204_NO_CONTENT)
def admin_add(shelf: str, name: str = GAME, admin: AdminUser = Depends(edit), db: Session = Depends(get_db)):
    try:
        service.add(db, shelf, name, admin.email)
    except service.DiscoveryError as e:
        raise HTTPException(e.status, e.message)
    adm.log(db, admin, "featured.add", name, shelf=shelf)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/admin/featured/{shelf}/{name}", status_code=status.HTTP_204_NO_CONTENT)
def admin_remove(shelf: str, name: str = GAME, admin: AdminUser = Depends(edit), db: Session = Depends(get_db)):
    service.remove(db, shelf, name)
    adm.log(db, admin, "featured.remove", name, shelf=shelf)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/featured/{shelf}/{name}/move", status_code=status.HTTP_204_NO_CONTENT)
def admin_move(shelf: str, direction: str = Query(..., pattern="^(up|down)$"), name: str = GAME, admin: AdminUser = Depends(edit),
               db: Session = Depends(get_db)):
    service.move(db, shelf, name, direction == "up")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
