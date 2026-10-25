"""Game health's API (H2). Admins: /admin/game-health. Owners: /game-health/mine. 404 while the flag is off."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...game_store import GameStore, get_game_store
from ...models import Game, User
from ...routers.games import _game_or_404, _player_name
from ...security import current_user
from ...storage import game_url
from . import service
from .models import GameHealth

router = APIRouter(tags=["game health"])
manage = require("games.health")

OWNER_RECHECK = timedelta(minutes=1)  # how often an owner can ask for a check of their game


class ProblemOut(BaseModel):
    code: str
    message: str


class HealthOut(BaseModel):
    game: str
    url: str
    owner: str | None
    status: str
    failures: int
    problems: list[ProblemOut]
    checked_at: datetime | None
    last_ok_at: datetime | None
    unhealthy_since: datetime | None
    hidden: str | None = None  # why it's hidden, if it is ("needs_fixing": a health check just hid it)


class Overview(BaseModel):
    counts: dict[str, int]  # by status, live games only; "unchecked" includes games never checked
    games: list[HealthOut]  # unhealthy first, then failing


def _out(row: GameHealth, game: Game) -> HealthOut:
    return HealthOut(game=game.name, url=game_url(game.name), owner=_player_name(game.owner, game.owner.email),
                     status=row.status, failures=row.failures, problems=row.problems or [], checked_at=row.checked_at,
                     last_ok_at=row.last_ok_at, unhealthy_since=row.unhealthy_since, hidden=game.hidden)


def _store(store: GameStore | None) -> GameStore:
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Game storage isn't set up, so games can't be checked")
    return store


@router.get("/admin/game-health", response_model=Overview)
def overview(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    live = (Game.hidden.is_(None), Game.name.not_in(service.NEVER))
    counts = dict(db.execute(select(GameHealth.status, func.count()).join(Game).where(*live).group_by(GameHealth.status)).all())
    checked = sum(counts.values())
    total = db.scalar(select(func.count()).select_from(Game).where(*live)) or 0
    counts["unchecked"] = counts.get("unchecked", 0) + total - checked
    rows = db.execute(select(GameHealth, Game).join(Game).where(*live, GameHealth.status.in_(("unhealthy", "failing")))
                      .order_by(GameHealth.status.desc(), GameHealth.unhealthy_since, GameHealth.game_name)).all()
    return Overview(counts=counts, games=[_out(h, g) for h, g in rows])


@router.post("/admin/game-health/games/{name}/check", response_model=HealthOut)
def admin_check(name: str, admin: AdminUser = Depends(manage), db: Session = Depends(get_db),
                store: GameStore | None = Depends(get_game_store)):
    """Check one game now."""
    game = _game_or_404(db, name)
    row = service.check_and_record(db, _store(store), game.name)
    adm.log(db, admin, "game_health.check", name, status=row.status)
    db.commit()
    return _out(row, game)


@router.post("/admin/game-health/scan")
def scan(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Check every live game again soon (the job works through them, a few a minute)."""
    n = db.execute(update(GameHealth).values(checked_at=None)).rowcount
    adm.log(db, admin, "game_health.scan", None)
    db.commit()
    return {"queued": n}


@router.get("/game-health/mine", response_model=list[HealthOut])
def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner's games that are unhealthy (failing a check once or twice isn't shown: it may pass next time)."""
    rows = db.execute(select(GameHealth, Game).join(Game).where(Game.owner_id == user.id, GameHealth.status == "unhealthy")).all()
    return [_out(h, g) for h, g in rows]


@router.post("/game-health/games/{name}/check", response_model=HealthOut)
def owner_check(name: str, user: User = Depends(current_user), db: Session = Depends(get_db),
                store: GameStore | None = Depends(get_game_store)):
    """The owner fixed it: check again now (at most once a minute)."""
    game = _game_or_404(db, name)
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That's not your game")
    row = db.get(GameHealth, name)
    if row is not None and row.checked_at is not None and service.now() - service._utc(row.checked_at) < OWNER_RECHECK:
        return _out(row, game)
    return _out(service.check_and_record(db, _store(store), name), game)
