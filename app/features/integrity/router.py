from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...models import Game, User
from ...security import current_user
from . import service
from .models import ScoreFlag
from .schemas import AdminFlagOut, DecisionIn, FlagOut

# main.py mounts this with the feature's flag: every route answers 404 while "integrity" is off.
router = APIRouter(tags=["integrity"])


def _player(flag: ScoreFlag) -> str:
    from ...routers.games import _player_name  # imported here: routers.games imports this package
    return _player_name(flag.user)


def _out(flag: ScoreFlag, admin: bool = False) -> FlagOut:
    data = dict(id=flag.id, game=flag.game_name, player_id=flag.user_id, player=_player(flag), reason=flag.reason, status=flag.status,
                stats=flag.stats, previous_best=flag.previous_best, created_at=flag.created_at, reviewed_at=flag.reviewed_at)
    return AdminFlagOut(**data, reviewed_by=flag.reviewed_by, note=flag.note) if admin else FlagOut(**data)


@router.get("/games/{name}/flags", response_model=list[FlagOut])
def game_flags(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The runs held back in your game, newest first, so you can see why a score you expected isn't on the board."""
    game = db.get(Game, name)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such game")
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the game's owner can see its flags")
    rows = db.scalars(select(ScoreFlag).options(joinedload(ScoreFlag.user)).where(ScoreFlag.game_name == name).order_by(ScoreFlag.id.desc()))
    return [_out(f) for f in rows.unique()]


@router.get("/admin/score-flags", response_model=list[AdminFlagOut])
def list_flags(
    status_: str = Query("open", alias="status", pattern="^(open|cleared|confirmed)$"),
    game: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    admin: AdminUser = Depends(require("scores.review")),
    db: Session = Depends(get_db),
):
    """Flagged runs waiting for a decision (or already decided), oldest first."""
    query = select(ScoreFlag).options(joinedload(ScoreFlag.user)).where(ScoreFlag.status == status_)
    if game:
        query = query.where(ScoreFlag.game_name == game)
    return [_out(f, admin=True) for f in db.scalars(query.order_by(ScoreFlag.id).limit(limit)).unique()]


def _decide(flag_id: int, outcome: str, body: DecisionIn, admin: AdminUser, db: Session) -> AdminFlagOut:
    flag = db.get(ScoreFlag, flag_id)
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such flag")
    if flag.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, f"That flag was already {flag.status}")
    service.decide(db, flag, outcome, admin.email, body.note)
    adm.log(db, admin, f"score_flag.{outcome}", flag.game_name, flag_id=flag.id, player_id=flag.user_id)
    db.commit()
    return _out(flag, admin=True)


@router.post("/admin/score-flags/{flag_id}/clear", response_model=AdminFlagOut)
def clear_flag(flag_id: int, body: DecisionIn, admin: AdminUser = Depends(require("scores.review")), db: Session = Depends(get_db)):
    """The run is genuine: it counts from now on (the board, awards and ranks), though not retroactively in a past tournament."""
    return _decide(flag_id, "cleared", body, admin, db)


@router.post("/admin/score-flags/{flag_id}/confirm", response_model=AdminFlagOut)
def confirm_flag(flag_id: int, body: DecisionIn, admin: AdminUser = Depends(require("scores.review")), db: Session = Depends(get_db)):
    """The run is not genuine: it stays out for good."""
    return _decide(flag_id, "confirmed", body, admin, db)
