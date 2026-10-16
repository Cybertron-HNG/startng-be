"""Explore games: the endpoints. The rules and checks are in app/explore.py."""
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import explore
from ..config import settings
from ..db import get_db
from ..errors import ApiError
from ..models import ExploreGame
from ..schemas import ExploreGameOut, ExplorePendingOut, ExploreSubmitOut

router = APIRouter(prefix="/explore", tags=["explore"])

# A 2 MB picture sent as base64 is about 2.8 MB, plus the text fields.
MAX_BODY = 3 * 1024 * 1024 + 64 * 1024
ATTEMPTS_PER_HOUR = 30  # tries (good or bad) per address: stops anyone hammering the picture checks
_attempts: dict[str, deque] = defaultdict(deque)
_accepted: dict[str, deque] = defaultdict(deque)


def _client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _within_hour(hits: dict[str, deque], address: str) -> deque:
    now = time.monotonic()
    recent = hits[address]
    while recent and now - recent[0] > 3600:
        recent.popleft()
    if len(hits) > 5000:  # forget addresses that have gone quiet
        for key in [k for k, v in hits.items() if not v and k != address]:
            del hits[key]
    return recent


def _out(game: ExploreGame) -> dict:
    stamp = int(game.cover_at.timestamp()) if game.cover_at else 0
    return {
        "id": game.id, "name": game.name, "creator": game.creator, "url": game.url, "host": game.host,
        "description": game.description, "created_at": game.created_at,
        "cover_url": f"/explore/games/{game.id}/cover?v={stamp}",
    }


def _require_admin(token: str | None) -> None:
    if not explore.is_admin(token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a start.ng moderator can do that")


async def _read_json(request: Request) -> dict:
    """The submission, read with a size cap whatever Content-Length says."""
    too_big = ApiError(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_big", "That submission is too big. Use a cover image under 2 MB.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise too_big
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks) or b"null")
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        raise ApiError(status.HTTP_400_BAD_REQUEST, "bad_request", "Send the game as JSON: name, creator, url, description and cover.")
    return payload


def _text(payload: dict, key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


@router.get("/games", response_model=list[ExploreGameOut])
def list_games(q: str | None = Query(None, max_length=80), db: Session = Depends(get_db)):
    """Listed games, newest first. ?q= keeps the ones whose name or creator contains every word."""
    query = (
        select(ExploreGame).where(ExploreGame.status == "approved")
        .order_by(ExploreGame.created_at.desc(), ExploreGame.id.desc()).limit(settings.explore_max_listed)
    )
    games = db.scalars(query).all()
    if q and q.strip():
        games = [g for g in games if explore.matches(g.name, g.creator, q)]
    return [_out(g) for g in games]


@router.post("/games", response_model=ExploreSubmitOut, status_code=status.HTTP_201_CREATED)
async def submit_game(request: Request, db: Session = Depends(get_db)):
    """List a game you host yourself. No sign-in. The reply has a removal key: shown once, keep it."""
    address = _client_address(request)
    attempts = _within_hour(_attempts, address)
    if len(attempts) >= ATTEMPTS_PER_HOUR:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "Too many tries from here, wait an hour and try again")
    attempts.append(time.monotonic())
    if len(_within_hour(_accepted, address)) >= settings.explore_per_hour:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", f"You can list {settings.explore_per_hour} games an hour. Try again later.")
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    if db.scalar(select(func.count()).select_from(ExploreGame).where(ExploreGame.created_at > hour_ago)) >= settings.explore_total_per_hour:
        raise ApiError(status.HTTP_429_TOO_MANY_REQUESTS, "busy", "Lots of games are being submitted right now. Try again in a little while.")

    payload = await _read_json(request)
    problems = []

    def note(field: str, message: str | None) -> None:
        if message:
            problems.append(explore.FieldProblem(f"bad_{field}", message, None, field))

    name, message = explore.check_name(_text(payload, "name"), "the name of your game", explore.NAME_LENGTH)
    note("name", message)
    creator, message = explore.check_name(_text(payload, "creator"), "your name or your studio's name", explore.CREATOR_LENGTH)
    note("creator", message)
    link, message = explore.check_url(_text(payload, "url"))
    note("url", message)
    description, message = explore.check_description(_text(payload, "description"))
    note("description", message)
    cover, message = explore.check_cover(_text(payload, "cover"))
    note("cover", message)
    if problems:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_submission", "Fix the highlighted fields and try again", problems)

    taken = db.scalar(select(ExploreGame).where(
        func.lower(ExploreGame.name) == name.lower(), func.lower(ExploreGame.creator) == creator.lower()))
    if taken:
        message = "This creator already has a game with this name in the listing."
        raise ApiError(status.HTTP_409_CONFLICT, "duplicate", message, [explore.FieldProblem("duplicate", message, None, "name")])
    if db.scalar(select(ExploreGame).where(func.lower(ExploreGame.url) == link.url.lower())):
        message = "This game is already listed."
        raise ApiError(status.HTTP_409_CONFLICT, "duplicate", message, [explore.FieldProblem("duplicate", message, None, "url")])

    key = explore.new_manage_key()
    game = ExploreGame(
        name=name, creator=creator, url=link.url, host=link.host, description=description, cover=cover,
        cover_at=datetime.now(timezone.utc), manage_hash=explore.hash_key(key),
        status="approved" if settings.explore_auto_approve else "pending",
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    _within_hour(_accepted, address).append(time.monotonic())
    return {"game": _out(game), "status": game.status, "manage_key": key}


@router.get("/games/pending", response_model=list[ExplorePendingOut])
def pending_games(x_admin_token: str | None = Header(None), db: Session = Depends(get_db)):
    """Moderators only: games waiting for approval (only when EXPLORE_AUTO_APPROVE is off), oldest first."""
    _require_admin(x_admin_token)
    games = db.scalars(select(ExploreGame).where(ExploreGame.status == "pending").order_by(ExploreGame.created_at)).all()
    return [{**_out(g), "status": g.status} for g in games]


@router.post("/games/{game_id}/approve", response_model=ExploreGameOut)
def approve_game(game_id: int, x_admin_token: str | None = Header(None), db: Session = Depends(get_db)):
    """Moderators only: show a waiting game in the list."""
    _require_admin(x_admin_token)
    game = db.get(ExploreGame, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that id")
    game.status = "approved"
    db.commit()
    return _out(game)


@router.get("/games/{game_id}/cover")
def game_cover(game_id: int, db: Session = Depends(get_db)):
    game = db.get(ExploreGame, game_id)
    if not game or game.status != "approved":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover for that game")
    return Response(content=game.cover, media_type="image/webp", headers={
        "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.delete("/games/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_game(
    game_id: int,
    x_manage_key: str | None = Header(None),
    x_admin_token: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Take a listing down. The creator sends the removal key from when they submitted (X-Manage-Key);
    a moderator sends X-Admin-Token."""
    game = db.get(ExploreGame, game_id)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that id")
    if explore.is_admin(x_admin_token) or (x_manage_key and explore.key_matches(x_manage_key, game.manage_hash)):
        db.delete(game)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if not x_manage_key and not x_admin_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Send the removal key you were given when you listed the game")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "That key does not match this game")
