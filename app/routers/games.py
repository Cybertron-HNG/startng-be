import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..game_store import GameStore, get_game_store
from ..models import Game, Score, User
from ..publish import PublishError, download_repo, game_name, parse_repo_url, read_game
from ..schemas import GameOut, LeaderboardEntry, PublishIn, PublishOut, ScoreIn, ScoreOut
from ..security import current_user
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


@router.post("/publish", response_model=PublishOut)
def publish(
    body: PublishIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    store: GameStore = Depends(require_store),
):
    """Publish (or update) a client-side game from a public GitHub repo at <name>.start.ng."""
    try:
        repo = parse_repo_url(body.repo_url, body.branch)
        name = game_name(body.name or repo.name)
    except PublishError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))

    # The first person to publish a name owns it. Folders already in the bucket without an
    # owner here (uploaded by hand, like dave/) are taken too.
    game = db.get(Game, name)
    if game and game.owner_id != user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, f'"{name}" is taken, pick another name')
    if not game and store.paths(name):
        raise HTTPException(status.HTTP_409_CONFLICT, f'"{name}" is already in use, pick another name')

    try:
        bundle = read_game(download_repo(repo))
    except PublishError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err))

    created = game is None
    if created:
        game = Game(name=name, owner_id=user.id, repo_url=repo.url, branch=repo.branch)
        db.add(game)
        try:
            db.commit()  # claim the name before uploading, so two people can't both get it
        except IntegrityError:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, f'"{name}" was just taken, pick another name')

    # Files a new version no longer has stay in the bucket; the Worker only serves what the
    # game links to, so they're harmless (same as the Worker's publish-game.js).
    try:
        store.put_game(name, {f.path: f.data for f in bundle.files})
    except Exception:
        log.exception("Uploading game %s failed", name)
        if created:
            db.delete(game)
            db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't upload the game, try again")

    game.repo_url, game.branch = repo.url, repo.branch
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


def _game_out(g: Game) -> GameOut:
    return GameOut(
        name=g.name,
        url=game_url(g.name),
        repo_url=g.repo_url,
        by=_player_name(g.owner, "A builder"),
        updated_at=g.updated_at,
    )


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
