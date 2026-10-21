"""Score integrity and fast leaderboards (PRD D2). What other features call (contract C5):

    from app.features.integrity import service as integrity

    integrity.ranked(db, "lane-dash", 10, only_users={1, 2, 3})   # a game's best runs, best first (rows with .user loaded)
    integrity.rank_of(db, user_id, "lane-dash")                    # that player's place, or None without a score

The ranking is the same one `app.routers.games._ranked` has always done (first stat, then the next ones, then whoever
got there first), but the database does the sorting, so it stays quick with tens of thousands of scores.
"""
import math
import time
from collections import deque
from typing import Any, Callable, Iterable

from sqlalchemy import case, func, null, select
from sqlalchemy.orm import Session, joinedload

from ... import leaderboard as boards
from ...config import settings
from ...errors import ApiError
from ...models import Game, Score

RATE_WINDOW_SECONDS = 60
CACHE_SECONDS = 30

_recent: dict[tuple[int, str], deque] = {}  # (player, game) -> when their recent scores came in
_cache: dict[tuple, tuple[float, Any]] = {}  # (game, limit) -> (when, answer)
_consistent: dict[tuple, bool] = {}  # (game, schema) -> does every row's `best` equal its first stat?


def clock() -> float:
    return time.monotonic()


def reset() -> None:
    _recent.clear()
    _cache.clear()
    _consistent.clear()


# --- limits and rate limit -------------------------------------------------------------------------------------

def check_limits(schema: list[dict], stats: dict[str, int]) -> None:
    """Refuse a run with a stat above the `max` its startng.json set."""
    for s in schema:
        top, value = s.get("max"), stats.get(s["key"])
        if top is not None and value is not None and value > top:
            raise ApiError(422, "score_out_of_range", f'"{s["key"]}" can be at most {top:,} in this game (you sent {value:,}).')


def check_rate(user_id: int, game: str) -> None:
    """At most `scores_per_minute` scores per player per game in any 60 seconds."""
    now = clock()
    recent = _recent.setdefault((user_id, game), deque())
    while recent and now - recent[0] >= RATE_WINDOW_SECONDS:
        recent.popleft()
    if len(recent) >= settings.scores_per_minute:
        err = ApiError(429, "rate_limited", "You're sending scores too fast. Try again in a moment.")
        err.headers = {"Retry-After": str(max(1, math.ceil(RATE_WINDOW_SECONDS - (now - recent[0]))))}
        raise err
    recent.append(now)
    if len(_recent) > 20_000:  # forget players who have gone quiet
        for key in [k for k, q in _recent.items() if not q or now - q[-1] >= RATE_WINDOW_SECONDS]:
            del _recent[key]


# --- ranking ---------------------------------------------------------------------------------------------------

def _schema(db: Session, game_name: str) -> list[dict] | None:
    game = db.get(Game, game_name)
    return None if game is None else (game.leaderboard or boards.DEFAULT)


def _first_stat_in_json(schema: list[dict]):
    """The first stat read out of the row's stats, the way `boards.stats_of` does: a row with no stats is a plain
    score, and a row that predates the schema may not have the stat at all (NULL = worst)."""
    key = schema[0]["key"]
    plain = Score.best if key == "score" else null()
    return case((Score.stats.is_(None), plain), else_=Score.stats[key].as_integer())


def _first_stat(db: Session, game_name: str, schema: list[dict]):
    """What to sort the first stat by. `scores.best` is that stat, and it has an index, so it is used whenever it
    agrees with the stats on every row of the game (checked once per game and schema). A game whose first stat
    changed after players had scores may not agree; then the stats themselves are read, which is slower but exact."""
    key = (game_name, tuple((s["key"], s["order"]) for s in schema))
    if key not in _consistent:
        disagree = select(Score.id).where(Score.game_name == game_name, (_first_stat_in_json(schema).is_(None)) | (_first_stat_in_json(schema) != Score.best)).limit(1)
        _consistent[key] = db.scalar(disagree) is None
    return Score.best if _consistent[key] else _first_stat_in_json(schema)


def _sort_key(schema: list[dict], stats: dict | None, best: int, updated_at, row_id: int) -> tuple:
    """The same order as app.routers.games._ranked: the stats in turn, then who got there first, then who was saved first."""
    return (boards.rank_key(schema, boards.stats_of(schema, stats, best)), updated_at, row_id)


def _filter(query, game_name: str, only_users: Iterable[int] | None):
    query = query.where(Score.game_name == game_name)
    return query if only_users is None else query.where(Score.user_id.in_(list(only_users)))


def ranked(db: Session, game_name: str, limit: int, only_users: Iterable[int] | None = None) -> list[Score]:
    """The game's best runs, best first. The database finds the top `limit` by the first stat; only the rows that tie
    with the last of them are sorted here, to apply the remaining stats and the time."""
    schema = _schema(db, game_name)
    if schema is None:
        return []
    value = _first_stat(db, game_name, schema)
    highest = schema[0]["order"] == "highest"
    order = [case((value.is_(None), 1), else_=0), value.desc() if highest else value.asc()]
    top = list(db.scalars(_filter(select(value), game_name, only_users).order_by(*order).limit(limit)))
    candidates = _filter(select(Score.id, Score.stats, Score.best, Score.updated_at), game_name, only_users)
    if len(top) == limit and top[-1] is not None:  # otherwise every row is a candidate
        candidates = candidates.where(value >= top[-1] if highest else value <= top[-1])
    rows = sorted(db.execute(candidates).all(), key=lambda r: _sort_key(schema, r.stats, r.best, r.updated_at, r.id))[:limit]
    by_id = {s.id: s for s in db.scalars(select(Score).options(joinedload(Score.user)).where(Score.id.in_([r.id for r in rows]))).unique()}
    return [by_id[r.id] for r in rows]


def rank_of(db: Session, user_id: int, game_name: str) -> int | None:
    """The player's place on the game's board, or None when they have no score. Counts the rows that beat their first
    stat in the database and compares only the ones that tie with it."""
    schema = _schema(db, game_name)
    mine = db.scalar(select(Score).where(Score.user_id == user_id, Score.game_name == game_name))
    if schema is None or mine is None:
        return None
    mine_key = _sort_key(schema, mine.stats, mine.best, mine.updated_at, mine.id)
    mine_first = boards.stats_of(schema, mine.stats, mine.best)[schema[0]["key"]]
    value = _first_stat(db, game_name, schema)
    on_board = Score.game_name == game_name
    if mine_first is None:
        better_first = select(func.count(Score.id)).where(on_board, value.is_not(None))
        tied = on_board & value.is_(None)
    else:
        beats = value > mine_first if schema[0]["order"] == "highest" else value < mine_first
        better_first = select(func.count(Score.id)).where(on_board, beats)
        tied = on_board & (value == mine_first)
    ahead = db.scalar(better_first)
    ties = db.execute(select(Score.id, Score.stats, Score.best, Score.updated_at).where(tied)).all()
    return ahead + sum(1 for r in ties if _sort_key(schema, r.stats, r.best, r.updated_at, r.id) < mine_key) + 1


# --- the 30 second cache ---------------------------------------------------------------------------------------

def cached(game_name: str, limit: int, build: Callable[[], Any]) -> Any:
    """`build()`'s answer for this game and limit, kept for CACHE_SECONDS. A new or removed score clears the game."""
    key = (game_name, limit)
    hit = _cache.get(key)
    if hit is not None and clock() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = build()
    _cache[key] = (clock(), value)
    return value


def invalidate(game_name: str) -> None:
    for key in [k for k in _cache if k[0] == game_name]:
        del _cache[key]
