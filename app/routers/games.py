import logging
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import covers
from ..config import settings
from ..db import get_db
from ..game_store import GameStore, get_game_store
from ..models import Game, PendingGame, Score, User
from ..publish import GameBundle, PublishError, Repo, download_repo, game_name, parse_repo_url, read_game
from ..schemas import GameOut, LeaderboardEntry, PendingInfo, PendingOut, PublishIn, PublishOut, ScoreIn, ScoreOut
from ..security import current_user, optional_user
from ..storage import game_url

log = logging.getLogger(__name__)
router = APIRouter(prefix="/games", tags=["games"])


def _player_name(user: User, fallback: str = "Player") -> str:
    return (user.character or {}).get("name") or user.name or fallback


def _game_or_404(db: Session, name: str) -> Game:
    game = db.get(Game, name)
    if not game:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that name")
    return game


def require_store(store: GameStore | None = Depends(get_game_store)) -> GameStore:
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Publishing isn't set up on this server yet")
    return store


def _linked_game(db: Session, repo_url: str) -> Game | None:
    """The live game a repo is linked to (GitHub links ignore case)."""
    return db.scalar(select(Game).where(func.lower(Game.repo_url) == repo_url.lower()))


def _check_name_free(db: Session, store: GameStore, name: str, owner_id: int | None) -> Game | None:
    """The game with this name if it's this owner's, None if the name is free; otherwise 409."""
    game = db.get(Game, name)
    if game and (owner_id is None or game.owner_id != owner_id):
        raise HTTPException(status.HTTP_409_CONFLICT, f'"{name}" is taken, pick another name')
    # Folders already in the bucket without an owner here (uploaded by hand, like dave/) are taken too.
    if not game and store.paths(name):
        raise HTTPException(status.HTTP_409_CONFLICT, f'"{name}" is already in use, pick another name')
    return game


def _download_and_check(repo: Repo) -> GameBundle:
    try:
        return read_game(download_repo(repo))
    except PublishError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))


def _put_live(db: Session, store: GameStore, game: Game | None, name: str, owner_id: int, repo: Repo, bundle: GameBundle) -> PublishOut:
    """Uploads a checked game under its name and records it, owned by owner_id."""
    created = game is None
    if created:
        game = Game(name=name, owner_id=owner_id, repo_url=repo.url, branch=repo.branch)
        db.add(game)
        try:
            db.commit()  # claim the name before uploading, so two people can't both get it
        except IntegrityError:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, f'"{name}" was just taken, pick another name')

    # Files a new version no longer has stay in the bucket; the Worker only serves what the
    # game links to, so they're harmless (same as the Worker's publish-game.js).
    # A repo file can't replace the cover the owner uploaded.
    files = {f.path: f.data for f in bundle.files if f.path != covers.COVER_FILE}
    try:
        store.put_game(name, files)
    except Exception:
        log.exception("Uploading game %s failed", name)
        if created:
            db.delete(game)
            db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't upload the game, try again")

    game.repo_url, game.branch = repo.url, repo.branch
    if game.cover != covers.COVER_FILE:  # an uploaded cover wins over the game's own
        game.cover = covers.repo_cover(list(files))
        game.cover_at = datetime.now(timezone.utc) if game.cover else None
    db.commit()
    return PublishOut(
        name=name,
        url=game_url(name),
        repo_url=repo.url,
        created=created,
        folder=bundle.folder,
        files=len(bundle.files),
        size_bytes=bundle.total_bytes,
        skipped=bundle.skipped,
    )


# Publishing without signing in: at most this many new (waiting) games per address per hour.
ANONYMOUS_PER_HOUR = 20
PENDING_DAYS = 30
_recent: dict[str, deque] = defaultdict(deque)


def _client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _rate_limit(request: Request) -> None:
    now = time.monotonic()
    hits = _recent[_client_address(request)]
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= ANONYMOUS_PER_HOUR:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many new games from here, try again in an hour")
    hits.append(now)


def _expired(when: datetime) -> bool:
    if when.tzinfo is None:  # SQLite drops the zone
        when = when.replace(tzinfo=timezone.utc)
    return when <= datetime.now(timezone.utc)


def _remove_expired(db: Session) -> None:
    db.execute(delete(PendingGame).where(PendingGame.expires_at <= datetime.now(timezone.utc)))
    db.commit()


@router.post("/publish", response_model=PublishOut | PendingOut)
def publish(
    body: PublishIn,
    request: Request,
    response: Response,
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
    store: GameStore = Depends(require_store),
):
    """Publish (or update) a client-side game from a public GitHub repo at <name>.start.ng.

    Signed in: it goes live on your account. Not signed in: a repo that's already live updates
    straight away; a new one is checked and waits (202) until someone opens approve_url, signs in
    with start.ng and approves it, which puts it live on their account.
    """
    try:
        repo = parse_repo_url(body.repo_url, body.branch)
        name = game_name(body.name or repo.name)
    except PublishError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))

    if user is not None:
        game = _check_name_free(db, store, name, user.id)
        return _put_live(db, store, game, name, user.id, repo, _download_and_check(repo))

    linked = _linked_game(db, repo.url)
    if linked is not None:
        # Already approved: this repo is that game, on its owner's account.
        if body.name and name != linked.name:
            raise HTTPException(status.HTTP_409_CONFLICT, f'This repo is live as "{linked.name}"; it can only update that game')
        return _put_live(db, store, linked, linked.name, linked.owner_id, repo, _download_and_check(repo))

    _rate_limit(request)
    _check_name_free(db, store, name, None)
    bundle = _download_and_check(repo)  # report problems now, before anyone approves
    _remove_expired(db)
    pending = PendingGame(
        code=secrets.token_urlsafe(12),
        name=name,
        repo_url=repo.url,
        branch=repo.branch,
        expires_at=datetime.now(timezone.utc) + timedelta(days=PENDING_DAYS),
    )
    db.add(pending)
    db.commit()
    response.status_code = status.HTTP_202_ACCEPTED
    approve_url = f"{settings.site_url.rstrip('/')}/approve/{pending.code}"
    return PendingOut(
        name=name,
        repo_url=repo.url,
        approve_url=approve_url,
        expires_at=pending.expires_at,
        folder=bundle.folder,
        files=len(bundle.files),
        size_bytes=bundle.total_bytes,
        skipped=bundle.skipped,
        message=(
            f"Checked and ready. Open {approve_url} to sign in with start.ng and put it live on your account. "
            "After that, publishing this repo again updates the game straight away."
        ),
    )


def _pending_or_404(db: Session, code: str) -> PendingGame:
    pending = db.get(PendingGame, code)
    if pending is None or _expired(pending.expires_at):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link has expired or was already used")
    return pending


@router.get("/publish/{code}", response_model=PendingInfo)
def pending_game(code: str, db: Session = Depends(get_db)):
    """What the approve page shows about a game waiting to be approved."""
    pending = _pending_or_404(db, code)
    taken = db.get(Game, pending.name) is not None and _linked_game(db, pending.repo_url) is None
    return PendingInfo(name=pending.name, repo_url=pending.repo_url, expires_at=pending.expires_at, available=not taken)


@router.post("/publish/{code}/approve", response_model=PublishOut)
def approve_game(
    code: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: GameStore = Depends(require_store),
):
    """Put a waiting game live on your account. Its repo is then linked to you for good."""
    pending = _pending_or_404(db, code)
    repo = parse_repo_url(pending.repo_url, pending.branch)
    linked = _linked_game(db, repo.url)
    if linked is not None and linked.owner_id != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This repo is already live on another start.ng account")
    name = linked.name if linked is not None else pending.name
    game = _check_name_free(db, store, name, user.id)
    out = _put_live(db, store, game, name, user.id, repo, _download_and_check(repo))
    # Every link for this repo is spent now: the repo belongs to this account.
    db.execute(delete(PendingGame).where(func.lower(PendingGame.repo_url) == repo.url.lower()))
    db.commit()
    return out


def _game_out(g: Game) -> GameOut:
    return GameOut(
        name=g.name,
        url=game_url(g.name),
        repo_url=g.repo_url,
        by=_player_name(g.owner, "A builder"),
        updated_at=g.updated_at,
        cover_url=_cover_url(g),
    )


def _cover_url(g: Game) -> str | None:
    """Served by the Worker from the game's folder. ?v= changes with the cover, so browsers
    and Cloudflare never show an old one."""
    if not g.cover:
        return None
    version = int(g.cover_at.timestamp()) if g.cover_at else 0
    return f"{game_url(g.name)}/{quote(g.cover)}?v={version}"


@router.get("", response_model=list[GameOut])
def list_games(db: Session = Depends(get_db)):
    return [_game_out(g) for g in db.scalars(select(Game).order_by(Game.updated_at.desc())).all()]


@router.get("/mine", response_model=list[GameOut])
def my_games(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The signed-in builder's own games, newest first (for their profile)."""
    query = select(Game).where(Game.owner_id == user.id).order_by(Game.updated_at.desc())
    return [_game_out(g) for g in db.scalars(query).all()]


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def unpublish(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: GameStore = Depends(require_store),
):
    """Take your game offline and free its name (its scores go with it)."""
    game = _game_or_404(db, name)
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the game's owner can remove it")
    store.delete_game(name)
    db.delete(game)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _own_game(db: Session, name: str, user: User) -> Game:
    game = _game_or_404(db, name)
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the game's owner can change its cover")
    return game


async def cover_upload(request: Request) -> bytes:
    """The picture, sent as the raw request body. Read with a size cap, whatever Content-Length says."""
    too_big = HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The picture must be 2 MB or less")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > covers.MAX_UPLOAD_BYTES:
        raise too_big
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > covers.MAX_UPLOAD_BYTES:
            raise too_big
        chunks.append(chunk)
    if not size:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send a PNG, JPEG or WebP picture")
    return b"".join(chunks)


@router.put("/{name}/cover", response_model=GameOut)
def upload_cover(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: GameStore = Depends(require_store),
    data: bytes = Depends(cover_upload),
):
    """Set your game's card picture. It's cropped to 640 x 400 and saved as WebP."""
    game = _own_game(db, name, user)
    try:
        cover = covers.make_cover(data)
    except covers.CoverError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))
    try:
        store.put_file(name, covers.COVER_FILE, cover, "image/webp")
    except Exception:
        log.exception("Uploading the cover of %s failed", name)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't save the picture, try again")
    game.cover, game.cover_at = covers.COVER_FILE, datetime.now(timezone.utc)
    db.commit()
    return _game_out(game)


@router.delete("/{name}/cover", response_model=GameOut)
def remove_cover(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: GameStore = Depends(require_store),
):
    """Remove the picture you uploaded. The game's own cover.png (if any) or the letter tile shows again."""
    game = _own_game(db, name, user)
    if game.cover == covers.COVER_FILE:
        store.delete_file(name, covers.COVER_FILE)
        game.cover = covers.repo_cover(store.paths(name))
        game.cover_at = datetime.now(timezone.utc) if game.cover else None
        db.commit()
    return _game_out(game)


@router.post("/{name}/scores", response_model=ScoreOut)
def submit_score(
    name: str,
    body: ScoreIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Save a signed-in player's score. Only their best score per game is kept."""
    _game_or_404(db, name)
    row = db.scalar(select(Score).where(Score.user_id == user.id, Score.game_name == name))
    improved = row is None or body.score > row.best
    if row is None:
        row = Score(user_id=user.id, game_name=name, best=body.score)
        db.add(row)
    elif improved:
        row.best = body.score
    try:
        db.commit()
    except IntegrityError:  # two first scores at once: keep the higher one
        db.rollback()
        row = db.scalar(select(Score).where(Score.user_id == user.id, Score.game_name == name))
        improved = body.score > row.best
        if improved:
            row.best = body.score
            db.commit()
    ahead = db.scalar(select(func.count()).select_from(Score).where(Score.game_name == name, Score.best > row.best))
    return ScoreOut(best=row.best, improved=improved, rank=ahead + 1)


@router.get("/{name}/leaderboard", response_model=list[LeaderboardEntry])
def leaderboard(name: str, limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    """A game's top players by best score. Ties go to whoever got there first."""
    _game_or_404(db, name)
    rows = db.scalars(
        select(Score).where(Score.game_name == name).order_by(Score.best.desc(), Score.updated_at.asc()).limit(limit)
    ).all()
    return [
        LeaderboardEntry(
            rank=i + 1,
            player_id=s.user_id,
            name=_player_name(s.user),
            color=((s.user.character or {}).get("style") or {}).get("primaryColor"),
            score=s.best,
        )
        for i, s in enumerate(rows)
    ]
