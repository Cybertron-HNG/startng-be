from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Game, User
from ...routers.games import _cover_url, _player_name
from ...security import current_user, optional_user
from . import service
from .models import Challenge, ChallengeAttempt

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/challenges", tags=["challenges"])

CODE = Path(pattern=r"^[\w-]{8,16}$")


class ChallengeIn(BaseModel):
    game: str = Field(min_length=1, max_length=40)
    run_id: int | None = None  # one of your runs (default: your best in the game)


class StatOut(BaseModel):
    key: str
    label: str
    value: int
    shown: str  # as players read it: "4,200" or "1:05"


class AttemptOut(BaseModel):
    best: str | None  # their best run since they took the challenge, as shown
    beaten: bool
    short_by: str | None  # "300": how far they are, on the first stat
    attempts: int | None = None  # filled for the challenger only


class ChallengeOut(BaseModel):
    code: str
    game: str
    game_title: str
    cover_url: str | None
    challenger: str
    character: dict | None
    stat: StatOut
    expires_at: datetime
    mine: bool  # the signed-in player made it
    attempt: AttemptOut | None  # the signed-in friend's progress (None: not accepted yet)
    takers: int  # how many accepted
    beaten_by: int  # how many beat it


def _attempt_out(c: Challenge, a: ChallengeAttempt, game: Game) -> AttemptOut:
    stat = service.schema_of(game)[0]
    best = a.best_stats.get(stat["key"]) if a.best_stats else None
    short = service.short_by(c, a, game)
    return AttemptOut(best=service.format_stat(stat, best) if best is not None else None, beaten=a.beaten_at is not None,
                      short_by=service.format_stat(stat, short) if short else None)


def _out(db: Session, c: Challenge, user: User | None) -> ChallengeOut:
    game = db.get(Game, c.game_name)
    stat = service.schema_of(game)[0]
    mine = user is not None and user.id == c.challenger_id
    attempt = db.get(ChallengeAttempt, (c.id, user.id)) if user is not None and not mine else None
    takers = db.scalar(select(func.count()).select_from(ChallengeAttempt).where(ChallengeAttempt.challenge_id == c.id)) or 0
    beaten_by = db.scalar(select(func.count()).select_from(ChallengeAttempt).where(
        ChallengeAttempt.challenge_id == c.id, ChallengeAttempt.beaten_at.is_not(None))) or 0
    value = c.stats.get(stat["key"], c.score)
    return ChallengeOut(
        code=c.code, game=game.name, game_title=" ".join(w.capitalize() for w in game.name.split("-")), cover_url=_cover_url(game),
        challenger=_player_name(c.challenger), character=c.challenger.character,
        stat=StatOut(key=stat["key"], label=stat["label"], value=value, shown=service.format_stat(stat, value)),
        expires_at=service._utc(c.expires_at), mine=mine, attempt=_attempt_out(c, attempt, game) if attempt else None,
        takers=takers, beaten_by=beaten_by)


def _fail(e: service.ChallengeError):
    raise HTTPException(e.status, e.message)


def _open(db: Session, code: str) -> Challenge:
    c = service.find(db, code)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This challenge has ended or doesn't exist")
    return c


@router.post("", response_model=ChallengeOut, status_code=status.HTTP_201_CREATED)
def create(body: ChallengeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Challenge friends with one of your runs. The score comes from the run log."""
    try:
        c = service.create(db, user, body.game, body.run_id)
    except service.ChallengeError as e:
        _fail(e)
    return _out(db, c, user)


@router.get("/mine", response_model=list[ChallengeOut])
def mine(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Your open challenges, newest first."""
    rows = db.scalars(select(Challenge).where(Challenge.challenger_id == user.id, Challenge.expires_at > service.now())
                      .order_by(Challenge.id.desc()).limit(service.MAX_ACTIVE)).all()
    return [_out(db, c, user) for c in rows if db.get(Game, c.game_name) is not None]


@router.get("/{code}", response_model=ChallengeOut)
def show(code: str = CODE, user: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """The challenge page's data. Anyone with the link can see it; a signed-in friend also sees their own progress."""
    return _out(db, _open(db, code), user)


@router.post("/{code}/accept", response_model=ChallengeOut)
def accept(code: str = CODE, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Take the challenge: your runs for this game count from now on."""
    c = _open(db, code)
    try:
        service.accept(db, c, user)
    except service.ChallengeError as e:
        _fail(e)
    return _out(db, c, user)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete(code: str = CODE, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = db.scalar(select(Challenge).where(Challenge.code == code))
    if c is None or c.challenger_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No challenge of yours with that link")
    db.delete(c)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
