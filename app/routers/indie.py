"""Games by independent creators: games made and hosted outside start.ng, listed on the homepage.

A signed-in creator sends the game's name, a link to play it, a short description and a cover
picture. start.ng doesn't host or run the game; it shows a card that opens the link. The cover is
remade as a card-sized WebP (app.covers), so nothing hidden in the upload is ever served.
"""
import base64
import binascii
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import covers
from ..db import get_db
from ..errors import ApiError
from ..models import IndieGame, IndiePlay, User
from ..schemas import IndieGameIn, IndieGameOut
from ..security import current_user, optional_user
from .games import PLAY_AGAIN_MINUTES, PLAYS_DAYS, PLAYS_KEPT_DAYS, _player_name, _rate_limit, visitor_of

router = APIRouter(prefix="/indie", tags=["indie"])

NAME_LEN = (2, 60)
DESCRIPTION_LEN = (10, 600)
MAX_URL = 300
SUBMITS_PER_HOUR = 10
_submits: dict[str, deque] = defaultdict(deque)  # recent submissions per address
DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,(.+)$", re.S)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _bad(code: str, message: str) -> ApiError:
    return ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, code, message)


def _text(value: str, field: str, size: tuple[int, int]) -> str:
    value = CONTROL.sub("", value or "").strip()
    if not size[0] <= len(value) <= size[1]:
        raise _bad(f"bad_{field}", f"The {field} must be {size[0]} to {size[1]} characters")
    return value


def _link(url: str) -> str:
    """A link players can open: https only, with a real host, no user:password in it."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if len(url) > MAX_URL or parts.scheme != "https" or not parts.hostname or "." not in parts.hostname or parts.username or parts.password:
        raise _bad("bad_url", "Use the game's full https:// link, like https://yourname.itch.io/your-game")
    return url


def _cover(data_url: str) -> bytes:
    match = DATA_URL.match(data_url or "")
    if not match:
        raise _bad("bad_cover", "Add a cover picture: PNG, JPEG or WebP")
    if len(match.group(2)) > covers.MAX_UPLOAD_BYTES * 4 // 3 + 4:
        raise ApiError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "cover_too_big", "The picture must be 2 MB or less")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise _bad("bad_cover", "That picture couldn't be read, try another")
    try:
        return covers.make_cover(raw)
    except covers.CoverError as err:
        raise _bad("bad_cover", str(err))


def _recent_plays(db: Session) -> dict[int, int]:
    since = datetime.now(timezone.utc) - timedelta(days=PLAYS_DAYS)
    rows = db.execute(select(IndiePlay.indie_id, func.count()).where(IndiePlay.at >= since).group_by(IndiePlay.indie_id))
    return {gid: count for gid, count in rows}


def _out(g: IndieGame, user: User | None = None, plays: int = 0) -> IndieGameOut:
    return IndieGameOut(
        id=g.id, name=g.name, url=g.url, description=g.description, by=_player_name(g.owner, "A creator"),
        cover_path=f"/indie/{g.id}/cover.webp", created_at=g.created_at, mine=bool(user and user.id == g.owner_id), plays=plays,
    )


@router.get("", response_model=list[IndieGameOut])
def list_indie_games(
    limit: int = Query(200, ge=1, le=200),
    sort: Literal["newest", "popular"] = "newest",
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    """Newest first, or most opened this week first (sort=popular; ties: newest first)."""
    plays = _recent_plays(db)
    games = db.scalars(select(IndieGame).order_by(IndieGame.created_at.desc(), IndieGame.id.desc())).all()
    if sort == "popular":
        games = sorted(games, key=lambda g: -plays.get(g.id, 0))
    return [_out(g, user, plays.get(g.id, 0)) for g in games[:limit]]


@router.post("/{game_id}/plays", status_code=status.HTTP_204_NO_CONTENT)
def count_indie_play(game_id: int, request: Request, db: Session = Depends(get_db)):
    """start.ng calls this when someone opens the game, to rank the most clicked. No sign-in needed."""
    if not db.get(IndieGame, game_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that id")
    visitor, now = visitor_of(request), datetime.now(timezone.utc)
    again = db.scalar(select(IndiePlay.id).where(
        IndiePlay.indie_id == game_id, IndiePlay.visitor == visitor, IndiePlay.at >= now - timedelta(minutes=PLAY_AGAIN_MINUTES),
    ).limit(1))
    if again is None:
        db.add(IndiePlay(indie_id=game_id, visitor=visitor, at=now))
    db.execute(delete(IndiePlay).where(IndiePlay.at < now - timedelta(days=PLAYS_KEPT_DAYS)))
    db.commit()


@router.post("", response_model=IndieGameOut, status_code=status.HTTP_201_CREATED)
def submit_indie_game(
    body: IndieGameIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _rate_limit(request, _submits, SUBMITS_PER_HOUR, "You've added a lot of games, try again in an hour")
    name = _text(body.name, "name", NAME_LEN)
    description = _text(body.description, "description", DESCRIPTION_LEN)
    url = _link(body.url)
    if db.scalar(select(IndieGame.id).where(IndieGame.url == url)):
        raise ApiError(status.HTTP_409_CONFLICT, "already_listed", "That game is already listed")
    cover = _cover(body.cover)
    game = IndieGame(owner_id=user.id, name=name, url=url, description=description, cover=cover)
    db.add(game)
    db.commit()
    db.refresh(game)
    return _out(game, user)


@router.get("/{game_id}/cover.webp")
def indie_cover(game_id: int, db: Session = Depends(get_db)):
    cover = db.scalar(select(IndieGame.cover).where(IndieGame.id == game_id))
    if cover is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that id")
    # A cover never changes (a new submission gets a new id), so browsers can keep it.
    return Response(cover, media_type="image/webp", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_indie_game(game_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    game = db.get(IndieGame, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that id")
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the creator who added it can remove it")
    db.delete(game)
    db.commit()
