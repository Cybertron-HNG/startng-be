from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import ApiError
from ...models import Game, User
from ...routers.games import _player_name
from ...security import current_user, optional_user
from . import service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["tips"])
GAME = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")


class TipIn(BaseModel):
    amount: int
    note: str | None = Field(default=None, max_length=service.NOTE_MAX)


class TipBoxOut(BaseModel):
    supporters: int  # different players who have tipped this game's creator
    amounts: list[int]
    can_tip: bool | None  # None when signed out
    reason: str | None  # why they can't, for the player
    coins: int | None  # what they can tip (coins they earned), when signed in


class TipSent(BaseModel):
    balance: int
    supporters: int


class TipOut(BaseModel):
    by: str
    amount: int
    note: str | None
    at: datetime


class ReceivedOut(BaseModel):
    count: int
    coins: int
    tips: list[TipOut]


def _listed(db: Session, name: str) -> Game:
    game = db.get(Game, name)
    if game is None or game.hidden:
        raise HTTPException(404, "Game not found")
    return game


@router.get("/games/{name}/tips", response_model=TipBoxOut)
def tip_box(name: str = GAME, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """What the game page's Tip the creator box shows: the supporters count and, signed in, whether they can tip."""
    game = _listed(db, name)
    out = TipBoxOut(supporters=service.supporters(db, game.owner_id), amounts=list(service.AMOUNTS), can_tip=None, reason=None, coins=None)
    if user is not None:
        out.reason = service.why_not(db, user.id, game)
        out.coins = service.spendable(db, user.id)
        out.can_tip = out.reason is None and out.coins >= min(service.AMOUNTS)
        if out.reason is None and not out.can_tip:
            out.reason = f"You need at least {min(service.AMOUNTS)} coins you earned by playing"
    return out


@router.post("/games/{name}/tips", response_model=TipSent, status_code=201)
def send_tip(body: TipIn, name: str = GAME, user: User = Depends(current_user), db: Session = Depends(get_db)):
    game = _listed(db, name)
    try:
        balance = service.send(db, user.id, game, body.amount, body.note)
    except service.TipError as err:
        raise ApiError(err.status, err.code, err.message)
    return TipSent(balance=balance, supporters=service.supporters(db, game.owner_id))


@router.get("/games/{name}/tips/received", response_model=ReceivedOut)
def received(name: str = GAME, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The tips and notes a game got, for its creator only (Your games)."""
    game = db.get(Game, name)
    if game is None or game.owner_id != user.id:
        raise HTTPException(404, "Game not found")
    count, coins = service.received_total(db, game.name)
    tips = [TipOut(by=_player_name(t.sender, "A player"), amount=t.amount, note=t.note, at=t.created_at) for t in service.received(db, game.name)]
    return ReceivedOut(count=count, coins=coins, tips=tips)
