from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_db
from ...models import User
from ...routers.games import _cover_url, _player_name
from ...routers.pages import _title
from ...security import current_user, optional_user
from ...storage import game_url
from . import service
from .models import Challenge, ChallengeAttempt
from .schemas import AttemptOut, CanOut, ChallengeIn, ChallengeOut, GameOut, PlayerOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/challenges", tags=["challenges"])

CODE = Path(pattern=r"^[\w-]{8,16}$")


def _refuse(e: service.Refused) -> HTTPException:
    return HTTPException(e.status, e.detail)


def _found(db: Session, code: str) -> Challenge:
    c = service.find(db, code)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This challenge has expired or was deleted")
    return c


def _player(u: User) -> PlayerOut:
    return PlayerOut(id=u.id, name=_player_name(u), character=u.character)


def _attempt(c: Challenge, a: ChallengeAttempt) -> AttemptOut:
    return AttemptOut(player=_player(a.user), best=a.best,
                      best_text=service.format_score(c.game, a.best) if a.best is not None else None,
                      beaten=a.beaten_at is not None, short_by=service.short_by(c.game, a.best, c.score))


def _out(db: Session, c: Challenge, viewer: User | None) -> ChallengeOut:
    mine = viewer is not None and viewer.id == c.challenger_id
    everyone = service.attempts(db, c)
    me = next((a for a in everyone if viewer and a.user_id == viewer.id), None)
    return ChallengeOut(
        code=c.code, url=f"{settings.site_url.rstrip('/')}/c/{c.code}", challenger=_player(c.challenger),
        game=GameOut(name=c.game_name, title=_title(c.game_name), url=game_url(c.game_name), cover_url=_cover_url(c.game),
                     stat=service.first_stat(c.game)),
        score=c.score, score_text=service.format_score(c.game, c.score), created_at=c.created_at, expires_at=c.expires_at,
        mine=mine, me=_attempt(c, me) if me else None,
        attempts=[_attempt(c, a) for a in everyone] if mine else [], takers=len(everyone),
    )


@router.post("", response_model=ChallengeOut, status_code=status.HTTP_201_CREATED)
def create(body: ChallengeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Challenge a friend with one of your runs (run_id) or your best run on a game (game). The score comes from the run
    log, never from the request."""
    try:
        c = service.create(db, user, body.run_id if body.run_id is not None else service.best_run(db, user, body.game))
    except service.Refused as e:
        raise _refuse(e)
    return _out(db, service.find(db, c.code), user)


@router.get("/can/{game}", response_model=CanOut)
def can(game: str = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Whether you can challenge a friend on a game: you need a run on it. The game's page asks before showing the button."""
    return CanOut(can=service.has_run(db, user, game))


@router.get("/{code}", response_model=ChallengeOut)
def get(code: str = CODE, viewer: User | None = Depends(optional_user), db: Session = Depends(get_db)):
    """Anyone with the link sees the challenge; signing in is only needed to take it on."""
    return _out(db, _found(db, code), viewer)


@router.post("/{code}/accept", response_model=ChallengeOut)
def accept(code: str = CODE, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Take it on: your runs on the game from now on are compared with it."""
    c = _found(db, code)
    try:
        service.accept(db, c, user)
    except service.Refused as e:
        raise _refuse(e)
    return _out(db, c, user)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
def delete(code: str = CODE, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        service.delete(db, _found(db, code), user)
    except service.Refused as e:
        raise _refuse(e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
