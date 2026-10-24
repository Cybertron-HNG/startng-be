"""Categories and ratings (T1). What other features read (PRD C12): `info(db, game)` gives a game's category, rating
average, rating count and smoothed score.

Categories are a fixed list. A game sets its own in startng.json ("category": "Puzzle"); the owner can also set it in Your
games. The file wins when the game is published or updated. A game with none is "Uncategorized" (None here).

Ratings are 1 to 5 stars, one per player, editable, and only from players who have played the game: they opened it from
start.ng (the Progress Engine's game_opens, C4) or saved a score in it. There is no waiting time. Stars show once a game has MIN_SHOWN ratings; ordering uses a smoothed average
so one 5-star vote doesn't outrank fifty 4.6s.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import manifest
from ...features.progress import service as progress
from ...features.progress.models import Run
from ...models import Game, Score
from .models import GameCatalog, GameRating

NAME = "catalog"

CATEGORIES = ["Action", "Racing", "Puzzle", "Card & board", "Arcade", "Adventure", "Sports", "Strategy", "Educational"]
MIN_SHOWN = 5  # stars show from this many ratings
PRIOR_STARS, PRIOR_WEIGHT = 3.5, 5  # the smoothing: every game starts with this many imaginary 3.5-star votes


class CatalogError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


# ---- categories

def normalize_category(value) -> str:
    """The list's spelling of a category ("card & board" -> "Card & board"), or SchemaError."""
    if isinstance(value, str):
        for c in CATEGORIES:
            if c.lower() == value.strip().lower():
                return c
    raise manifest.SchemaError(f'category must be one of: {", ".join(CATEGORIES)}')


def _row(db: Session, game_name: str) -> GameCatalog:
    row = db.get(GameCatalog, game_name)
    if row is None:
        row = GameCatalog(game_name=game_name, rating_count=0)
        db.add(row)
    return row


def _save_file_category(db: Session, game, value: str) -> None:
    """startng.json said so: it wins over what the owner chose (runs when a game is published or updated)."""
    row = _row(db, game.name)
    row.category, row.category_source = value, "file"


manifest.register_section("category", normalize_category, _save_file_category)


def set_category(db: Session, game_name: str, category: str | None) -> GameCatalog:
    """The owner's choice in Your games (None clears it). A later publish whose startng.json has a category replaces it."""
    row = _row(db, game_name)
    row.category = normalize_category(category) if category else None
    row.category_source = "owner" if category else None
    db.commit()
    return row


# ---- ratings

def smoothed(avg: float | None, count: int) -> float:
    return (PRIOR_STARS * PRIOR_WEIGHT + (avg or 0) * count) / (PRIOR_WEIGHT + count)


@dataclass
class Info:
    game: str
    category: str | None
    rating_count: int
    rating_avg: float | None  # None until the game has MIN_SHOWN ratings
    score: float  # smoothed, for ordering


def _info(game_name: str, row: GameCatalog | None) -> Info:
    count = row.rating_count if row else 0
    avg = row.rating_avg if row else None
    return Info(game=game_name, category=row.category if row else None, rating_count=count,
                rating_avg=round(avg, 1) if avg is not None and count >= MIN_SHOWN else None, score=round(smoothed(avg, count), 3))


def info(db: Session, game_name: str) -> Info:
    return _info(game_name, db.get(GameCatalog, game_name))


def all_info(db: Session) -> list[Info]:
    """Every visible game's category and rating (one entry per game that has either)."""
    rows = db.scalars(select(GameCatalog).join(Game, Game.name == GameCatalog.game_name).where(Game.hidden.is_(None))).all()
    return [_info(r.game_name, r) for r in rows]


def refresh(db: Session, game_name: str) -> GameCatalog:
    """Recount a game's ratings (after one was added, changed or removed). The caller commits."""
    count, avg = db.execute(select(func.count(), func.avg(GameRating.stars)).where(GameRating.game_name == game_name)).one()
    row = _row(db, game_name)
    row.rating_count, row.rating_avg = count, float(avg) if avg is not None else None
    return row


def has_played(db: Session, user_id: int, game_name: str) -> bool:
    """They opened the game from start.ng, or they saved a score in it (an open is not recorded when someone reaches a game by
    its own address, or while their sign-in had run out, but a score is always theirs)."""
    if progress.game_open(db, user_id, game_name) is not None:
        return True
    scored = db.scalar(select(func.count()).select_from(Score).where(Score.user_id == user_id, Score.game_name == game_name))
    ran = db.scalar(select(func.count()).select_from(Run).where(Run.user_id == user_id, Run.game_name == game_name))
    return bool(scored or ran)


def can_rate(db: Session, user_id: int, game: Game, at: datetime | None = None) -> tuple[bool, str | None]:
    """Whether the player may rate the game now, and if not why (shown to them). Anyone who has played it may, for as long as it
    is available."""
    if game.owner_id == user_id:
        return False, "You can't rate your own game"
    if not has_played(db, user_id, game.name):
        return False, "Play the game before you rate it"
    return True, None


def rate(db: Session, user_id: int, game_name: str, stars: int, at: datetime | None = None) -> GameRating:
    game = db.get(Game, game_name)
    if game is None or game.hidden is not None:
        raise CatalogError("no_game", "There's no game with that name", 404)
    if not 1 <= stars <= 5:
        raise CatalogError("bad_stars", "Ratings are from 1 to 5 stars")
    ok, why = can_rate(db, user_id, game, at)
    if not ok:
        raise CatalogError("cant_rate", why or "You can't rate this game", 403)
    row = db.scalar(select(GameRating).where(GameRating.user_id == user_id, GameRating.game_name == game_name))
    if row is None:
        row = GameRating(user_id=user_id, game_name=game_name, stars=stars)
        db.add(row)
    else:
        row.stars = stars
    db.flush()
    refresh(db, game_name)
    db.commit()
    return row


def remove_rating(db: Session, rating: GameRating) -> None:
    name = rating.game_name
    db.delete(rating)
    db.flush()
    refresh(db, name)
    db.commit()
