from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import admin as admins
from ...activity import record
from ...db import get_db
from ...models import Game, User
from ...routers.games import _cover_url, _player_name
from ...routers.pages import _title
from ...security import current_user, optional_user
from ...storage import game_url
from . import service
from .models import Jam, JamVote
from .schemas import SLUG, EntryOut, JamChange, JamIn, JamOut, JamPageOut, VoteIn

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["jams"])
manage = admins.require("jams.manage")
JAM = Path(pattern=SLUG)


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def jam_out(db: Session, jam: Jam) -> JamOut:
    return JamOut(slug=jam.slug, title=jam.title, brief=jam.brief, opens_at=_utc(jam.opens_at),
                  entries_close_at=_utc(jam.entries_close_at), voting_ends_at=_utc(jam.voting_ends_at),
                  phase=service.phase(jam), entries=len(service.entries(db, jam)))


def _jam_or_404(db: Session, slug: str) -> Jam:
    jam = service.by_slug(db, slug)
    if jam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No jam here")
    return jam


@router.get("/jams", response_model=list[JamOut])
def list_jams(db: Session = Depends(get_db)):
    """Every jam, newest first (the ones not open yet too, so players can see what's coming)."""
    return [jam_out(db, j) for j in service.all_jams(db)]


@router.get("/jams/{slug}", response_model=JamPageOut)
def jam_page(slug: str = JAM, viewer: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    jam = _jam_or_404(db, slug)
    over = service.phase(jam) == "over"
    mine = service.votes_of(db, jam, viewer.id) if viewer else set()
    out = []
    for e in service.entries(db, jam):
        why = service.why_not_vote(db, jam, viewer.id, e.game) if viewer else "Sign in to vote"
        out.append(EntryOut(game=e.game.name, title=_title(e.game.name), url=game_url(e.game.name), cover_url=_cover_url(e.game),
                            by=_player_name(e.game.owner, "A builder"), votes=e.votes if over else None, winner=e.winner,
                            voted=e.game.name in mine, can_vote=why is None and e.game.name not in mine,
                            why_not=None if e.game.name in mine else why))
    return JamPageOut(jam=jam_out(db, jam), entries=out)


@router.post("/jams/{slug}/votes", status_code=status.HTTP_201_CREATED)
def vote(body: VoteIn, slug: str = JAM, user: User = Depends(current_user), db: Session = Depends(get_db)):
    jam = _jam_or_404(db, slug)
    game = db.get(Game, body.game)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That game isn't in this jam")
    why = service.why_not_vote(db, jam, user.id, game)
    if why:
        raise HTTPException(status.HTTP_403_FORBIDDEN, why)
    db.add(JamVote(jam_id=jam.id, game_name=game.name, user_id=user.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already voted for this game")
    return {"voted": True}


@router.delete("/jams/{slug}/votes/{game}", status_code=status.HTTP_204_NO_CONTENT)
def unvote(slug: str = JAM, game: str = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Take a vote back, while voting is open."""
    jam = _jam_or_404(db, slug)
    if service.phase(jam) != "voting":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Voting is only open during the jam's voting week")
    row = db.query(JamVote).filter_by(jam_id=jam.id, game_name=game, user_id=user.id).first()
    if row:
        db.delete(row)
        db.commit()


# ---- admins (the "Jams" tab on start.ng/admin)

@router.get("/admin/jams", response_model=list[JamOut])
def admin_list(admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    return [jam_out(db, j) for j in service.all_jams(db)]


@router.post("/admin/jams", response_model=JamOut, status_code=status.HTTP_201_CREATED)
def admin_create(body: JamIn, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    if service.by_slug(db, body.slug):
        raise HTTPException(status.HTTP_409_CONFLICT, "A jam with that short name already exists")
    jam = Jam(**body.model_dump())
    db.add(jam)
    record(db, admin.user, "admin", "jam.created", body.slug)
    db.commit()
    return jam_out(db, jam)


@router.patch("/admin/jams/{slug}", response_model=JamOut)
def admin_change(body: JamChange, slug: str = JAM, admin: admins.AdminUser = Depends(manage), db: Session = Depends(get_db)):
    jam = _jam_or_404(db, slug)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(jam, k, v)
    if not _utc(jam.opens_at) < _utc(jam.entries_close_at) < _utc(jam.voting_ends_at):
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The dates must be in order: opens, entries close, voting ends")
    record(db, admin.user, "admin", "jam.changed", slug)
    db.commit()
    return jam_out(db, jam)
