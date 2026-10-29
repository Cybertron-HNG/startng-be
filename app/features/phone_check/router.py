"""Phone check's API (Ziki #14). Owners: /games/{name}/phone-check. Admins: /admin/phone-checks. 404 while the flag
is off."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...models import Game, User
from ...routers.games import _game_or_404, _player_name
from ...security import current_user
from ...storage import game_url
from . import service
from .models import PhoneCheck

router = APIRouter(tags=["phone check"])
manage = require("games.phone_check")

OWNER_RECHECK = timedelta(minutes=1)  # how often an owner can ask for another check of their game


class FindingOut(BaseModel):
    code: str
    message: str


class PhoneCheckOut(BaseModel):
    game: str
    url: str
    owner: str | None = None  # admins only
    status: str  # unchecked, ok, worth_a_look, error
    method: str | None  # browser or files
    findings: list[FindingOut]
    checked_at: datetime | None


class Overview(BaseModel):
    counts: dict[str, int]  # by status
    games: list[PhoneCheckOut]  # worth a look, newest first


def _out(row: PhoneCheck | None, game: Game, for_admin: bool = False) -> PhoneCheckOut:
    return PhoneCheckOut(game=game.name, url=game_url(game.name), status=row.status if row else "unchecked",
                         owner=_player_name(game.owner, game.owner.email) if for_admin and game.owner else None,
                         method=row.method if row else None, findings=(row.findings or []) if row else [],
                         checked_at=row.checked_at if row else None)


def _own_game(db: Session, name: str, user: User) -> Game:
    game = _game_or_404(db, name)
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That's not your game")
    return game


@router.get("/games/{name}/phone-check", response_model=PhoneCheckOut)
def mine(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner's latest phone check of their game."""
    game = _own_game(db, name, user)
    return _out(db.get(PhoneCheck, name), game)


@router.post("/games/{name}/phone-check", response_model=PhoneCheckOut)
def check_again(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner changed something: check again soon (the background job, within a minute or two)."""
    game = _own_game(db, name, user)
    row = db.get(PhoneCheck, name)
    if row is None or row.status != "unchecked":
        if row is not None and row.checked_at is not None and service.now() - service._utc(row.checked_at) < OWNER_RECHECK:
            return _out(row, game)
        row = service.queue(db, name)
        db.commit()
    return _out(row, game)


@router.get("/admin/phone-checks", response_model=Overview)
def overview(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    live = (Game.hidden.is_(None), Game.name.not_in(service.NEVER))
    counts = dict(db.execute(select(PhoneCheck.status, func.count()).join(Game).where(*live).group_by(PhoneCheck.status)).all())
    rows = db.execute(select(PhoneCheck, Game).join(Game).where(*live, PhoneCheck.status == "worth_a_look")
                      .order_by(PhoneCheck.checked_at.desc())).all()
    return Overview(counts=counts, games=[_out(r, g, for_admin=True) for r, g in rows])


@router.post("/admin/phone-checks/scan")
def scan(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Check every live game (once, for games published before the phone check existed). The job works through them."""
    names = db.scalars(select(Game.name).where(Game.hidden.is_(None), Game.name.not_in(service.NEVER))).all()
    for name in names:
        service.queue(db, name)
    adm.log(db, admin, "phone_check.scan", None, games=len(names))
    db.commit()
    return {"queued": len(names)}
