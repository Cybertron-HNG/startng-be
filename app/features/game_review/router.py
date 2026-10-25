"""Game review's API (F2). Admins: /admin/game-review. Owners: /game-review/mine. 404 while the flag is off."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...admin import AdminUser, require
from ...db import get_db
from ...game_store import GameStore, get_game_store
from ...models import Game, User
from ...routers.games import _cover_url, _player_name
from ...security import current_user
from ...storage import game_url
from . import service
from .models import GameReview

router = APIRouter(tags=["game review"])
manage = require("games.review")


class ReviewOut(BaseModel):
    game: str
    url: str
    repo_url: str
    cover_url: str | None
    description: str | None
    owner: str | None  # for admins: their name or email
    status: str
    reason: str | None
    reason_label: str | None
    note: str | None
    rounds: int
    submitted_at: datetime
    decided_at: datetime | None
    decided_by: str | None  # for admins only
    meets_standards: bool | None
    health: str | None  # the game's latest health check, when game health is on ("ok", "failing", ...)


class Queue(BaseModel):
    reasons: dict[str, str]  # what an admin can pick when rejecting
    pending: list[ReviewOut]  # oldest first
    rejected: list[ReviewOut]  # newest first, the last 50


class RejectIn(BaseModel):
    reason: str
    note: str | None = Field(None, max_length=1000)


def _health(db: Session, name: str) -> str | None:
    try:
        from ..game_health.models import GameHealth
    except ImportError:  # pragma: no cover
        return None
    row = db.get(GameHealth, name)
    return row.status if row else None


def _out(db: Session, r: GameReview, for_admin: bool) -> ReviewOut:
    g = r.game
    return ReviewOut(
        game=g.name, url=game_url(g.name), repo_url=g.repo_url, cover_url=_cover_url(g), description=g.description,
        owner=_player_name(g.owner, g.owner.email) if for_admin else None, status=r.status, reason=r.reason,
        reason_label=service.REJECT_REASONS.get(r.reason) if r.reason else None, note=r.note, rounds=r.rounds,
        submitted_at=r.submitted_at, decided_at=r.decided_at, decided_by=r.decided_by if for_admin else None,
        meets_standards=g.meets_standards, health=_health(db, g.name) if for_admin else None,
    )


def _review_or_404(db: Session, name: str) -> GameReview:
    r = db.get(GameReview, name)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That game isn't waiting for review")
    return r


@router.get("/admin/game-review", response_model=Queue)
def queue(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    pending = db.scalars(select(GameReview).where(GameReview.status == "pending").order_by(GameReview.submitted_at)).all()
    rejected = db.scalars(select(GameReview).where(GameReview.status == "rejected")
                          .order_by(GameReview.decided_at.desc()).limit(50)).all()
    return Queue(reasons=service.REJECT_REASONS, pending=[_out(db, r, True) for r in pending],
                 rejected=[_out(db, r, True) for r in rejected])


@router.post("/admin/game-review/{name}/approve", response_model=ReviewOut)
def approve(name: str, admin: AdminUser = Depends(manage), db: Session = Depends(get_db),
            store: GameStore | None = Depends(get_game_store)):
    """List the game (and email its owner). Rejected games can be approved too, if an admin changes their mind."""
    r = _review_or_404(db, name)
    if r.status == "approved":
        return _out(db, r, True)
    adm.log(db, admin, "game_review.approved", name)
    service.approve(db, store, r, admin.email)  # commits
    return _out(db, r, True)


@router.post("/admin/game-review/{name}/reject", response_model=ReviewOut)
def reject(name: str, body: RejectIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Keep it unlisted and tell the owner why. They fix it and push, and it comes back to the queue."""
    if body.reason not in service.REJECT_REASONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick one of the reasons")
    if body.reason == "other" and not (body.note or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, 'Say what\'s wrong in the note when the reason is "something else"')
    r = _review_or_404(db, name)
    if r.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a game waiting for review can be rejected")
    adm.log(db, admin, "game_review.rejected", name, reason=body.reason)
    service.reject(db, r, body.reason, body.note, admin.email)  # commits
    return _out(db, r, True)


@router.get("/game-review/mine", response_model=list[ReviewOut])
def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner's games that are waiting for review or weren't approved, and why."""
    rows = db.scalars(select(GameReview).join(Game).where(
        Game.owner_id == user.id, GameReview.status.in_(("pending", "rejected")))).all()
    return [_out(db, r, False) for r in rows]
