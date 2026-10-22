from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...routers.games import _player_name
from ...security import current_user, optional_user
from . import badges, service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["profiles"])


class SettingsOut(BaseModel):
    public: bool
    show_favorites: bool
    show_ranks: bool
    notice_seen: bool  # false until they've seen the first-time "your page is public" notice


class SettingsIn(BaseModel):
    public: bool | None = None
    show_favorites: bool | None = None
    show_ranks: bool | None = None
    notice_seen: bool | None = None


class BadgeOut(BaseModel):
    key: str
    name: str
    description: str
    earned_at: datetime | None = None


class BestOut(BaseModel):
    game: str
    title: str
    best: str
    rank: int | None


class PlayedOut(BaseModel):
    game: str
    title: str
    runs: int


class FavoriteOut(BaseModel):
    game: str
    title: str


class ProfileOut(BaseModel):
    """Everything on a player's page. Never an email address."""
    id: int
    slug: str
    name: str
    character: dict | None
    level: int
    xp: int
    current_streak: int
    best_streak: int
    games_played: int
    runs: int
    badges: list[BadgeOut]
    bests: list[BestOut] | None  # null: the player hides ranks
    most_played: list[PlayedOut]
    favorites: list[FavoriteOut] | None  # null: the player hides favorites
    tournament_wins: int
    tournaments_played: int
    tournament_points: int
    me: bool
    settings: SettingsOut | None  # only for the player themselves


def _out(db: Session, user: User, me: bool) -> ProfileOut:
    p = service.build(db, user)
    return ProfileOut(
        id=user.id, slug=service.slug_for(user), name=_player_name(user), character=user.character, level=p.level, xp=p.xp,
        current_streak=p.current_streak, best_streak=p.best_streak, games_played=p.games_played, runs=p.runs,
        badges=[BadgeOut(**b) for b in p.badges], bests=None if p.bests is None else [BestOut(**b.__dict__) for b in p.bests],
        most_played=[PlayedOut(**m) for m in p.most_played], favorites=None if p.favorites is None else [FavoriteOut(**f) for f in p.favorites],
        tournament_wins=p.wins, tournaments_played=p.tournaments_played, tournament_points=p.tournament_points, me=me,
        settings=SettingsOut(**{k: getattr(p.settings, k) for k in SettingsOut.model_fields}) if me else None)


@router.get("/players/{user_id}/profile", response_model=ProfileOut)
def player(user_id: int, viewer: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """A player's page. 404 for an unknown player, and for a private page unless it's your own."""
    user = db.get(User, user_id)
    me = viewer is not None and viewer.id == user_id
    if user is None or not (me or service.is_public(db, user_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No player page here")
    return _out(db, user, me)


@router.get("/me/profile", response_model=ProfileOut)
def my_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, user, True)


@router.put("/me/profile-settings", response_model=SettingsOut)
def change_settings(body: SettingsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = service.update_settings(db, user.id, **body.model_dump())
    return SettingsOut(**{k: getattr(row, k) for k in SettingsOut.model_fields})


@router.get("/badges", response_model=list[BadgeOut])
def catalog():
    """Every badge a player can earn."""
    return [BadgeOut(key=b.key, name=b.name, description=b.description) for b in badges.CATALOG]
