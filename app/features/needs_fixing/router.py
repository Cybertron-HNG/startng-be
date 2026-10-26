"""The "needs fixing" label (H2). Admins: /admin/needs-fixing. Owners: /needs-fixing. 404 while the flag is off.

    admin marks a game  ->  hidden everywhere, waiting for its owner
    owner: "I fixed it" ->  back in the admins' queue (still hidden)
    admin puts it back  ->  live again          (or sends it back with a new note)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import admin as adm
from ...activity import record
from ...admin import AdminUser, require
from ...db import get_db
from ...game_store import GameStore, get_game_store
from ...models import Game, User
from ...routers.games import _cover_url, _game_or_404, _player_name
from ...security import current_user
from ...storage import game_url
from . import service
from .models import FixRequest
from .schemas import FixRequestOut, MarkIn, NoteIn
from .service import REASON

router = APIRouter(tags=["needs fixing"])
manage = require("games.needs_fixing")


def _out(r: FixRequest, for_admin: bool) -> FixRequestOut:
    g = r.game
    return FixRequestOut(
        id=r.id, game=g.name, game_url=game_url(g.name), cover_url=_cover_url(g),
        owner=_player_name(g.owner, g.owner.email) if for_admin and g.owner else None,
        status=r.status, note=r.note, owner_note=r.owner_note, rounds=r.rounds,
        created_by=r.created_by if for_admin else None, automatic=r.created_by == service.HEALTH_CHECK,
        created_at=r.created_at, owner_fixed_at=r.owner_fixed_at,
    )


def _request_or_404(db: Session, request_id: int) -> FixRequest:
    r = db.get(FixRequest, request_id)
    if r is None or r.status == "closed":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request, or it's already closed")
    return r


# ---- admins


@router.get("/admin/needs-fixing", response_model=list[FixRequestOut])
def queue(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Open requests: the ones the owner says are fixed first (they need a look), then the ones waiting on owners."""
    rows = db.scalars(select(FixRequest).where(FixRequest.status != "closed").order_by(FixRequest.created_at.desc())).all()
    rows = sorted(rows, key=lambda r: r.status != "owner_fixed")
    return [_out(r, True) for r in rows]


@router.post("/admin/needs-fixing/games/{name}", response_model=FixRequestOut, status_code=status.HTTP_201_CREATED)
def mark(name: str, body: MarkIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db),
         store: GameStore | None = Depends(get_game_store)):
    """Mark a game as needing a fix. It's hidden everywhere until an admin puts it back; the owner sees the note."""
    game = _game_or_404(db, name)
    if service.open_request(db, name):
        raise HTTPException(status.HTTP_409_CONFLICT, "This game is already marked as needing a fix")
    adm.log(db, admin, "game.needs_fixing", name)
    return _out(service.mark(db, store, game, body.note, admin.email), True)  # commits


@router.post("/admin/needs-fixing/{request_id}/approve", response_model=FixRequestOut)
def approve(request_id: int, admin: AdminUser = Depends(manage), db: Session = Depends(get_db),
            store: GameStore | None = Depends(get_game_store)):
    """It's fixed: close the request and put the game back (unless it's also hidden for another reason)."""
    r = _request_or_404(db, request_id)
    r.status, r.closed_by, r.closed_at = "closed", admin.email, adm.now()
    adm.log(db, admin, "game.fix_approved", r.game_name)
    if r.game.hidden == REASON:
        adm.show_game(db, store, r.game)  # commits
    else:
        db.commit()
    return _out(r, True)


@router.post("/admin/needs-fixing/{request_id}/reopen", response_model=FixRequestOut)
def send_back(request_id: int, body: MarkIn, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Still broken: back to the owner with a new note. The game stays hidden."""
    r = _request_or_404(db, request_id)
    r.status, r.note, r.rounds, r.owner_fixed_at = "waiting_owner", body.note.strip(), r.rounds + 1, None
    adm.log(db, admin, "game.fix_sent_back", r.game_name)
    db.commit()
    return _out(r, True)


# ---- owners


@router.get("/needs-fixing/mine", response_model=list[FixRequestOut])
def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner's games that need a fix, with what's wrong."""
    rows = db.scalars(select(FixRequest).join(Game).where(Game.owner_id == user.id, FixRequest.status != "closed")).all()
    return [_out(r, False) for r in rows]


@router.post("/needs-fixing/{request_id}/fixed", response_model=FixRequestOut)
def owner_fixed(request_id: int, body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The owner says it's fixed (push the fix first: start.ng updates from the repo). An admin then takes a look."""
    r = _request_or_404(db, request_id)
    if r.game.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such request, or it's already closed")
    if r.status == "owner_fixed":
        return _out(r, False)
    r.status, r.owner_note, r.owner_fixed_at = "owner_fixed", (body.note or "").strip() or None, adm.now()
    record(db, user, "developer", "game.owner_fixed", r.game_name)
    db.commit()
    return _out(r, False)
