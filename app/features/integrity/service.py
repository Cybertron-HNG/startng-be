"""Score integrity and fast leaderboards (PRD D2). What other features call (contract C5):

    from app.features.integrity import service as integrity

    integrity.ranked(db, "lane-dash", 10, only_users={1, 2, 3})   # a game's best runs, best first (rows with .user loaded)
    integrity.rank_of(db, user_id, "lane-dash")                    # that player's place, or None without a score

The ranking is the same one `app.routers.games._ranked` has always done (first stat, then the next ones, then whoever
got there first), but the database does the sorting, so it stays quick with tens of thousands of scores.
"""
import math
import time
from datetime import datetime, timezone
from collections import deque
from typing import Any, Callable, Iterable

from sqlalchemy import String, case, cast, func, null, or_, select
from sqlalchemy.orm import Session, joinedload

from ... import leaderboard as boards
from ...config import settings
from ...errors import ApiError
from ...models import Game, Score
from ..progress import service as progress
from .models import ScoreFlag

RATE_WINDOW_SECONDS = 60
CACHE_SECONDS = 30
EDGE_SECONDS = 300  # how long a game's 99th percentile is kept

_recent: dict[tuple[int, str], deque] = {}  # (player, game) -> when their recent scores came in
_cache: dict[tuple, tuple[float, Any]] = {}  # (game, limit) -> (when, answer)
_consistent: dict[tuple, bool] = {}  # (game, schema) -> does every row's `best` equal its first stat?
_edges: dict[tuple, tuple[float, int | None]] = {}  # (game, schema) -> (when, the 99th percentile of the first stat)


def clock() -> float:
    return time.monotonic()


def reset() -> None:
    _recent.clear()
    _cache.clear()
    _consistent.clear()
    _edges.clear()


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


def _no_stats():
    """A plain-score row: no stats at all. Old rows hold SQL NULL; a row saved with None through the ORM holds the JSON
    value null. Both mean the same, and `boards.stats_of` reads both as {"score": best}."""
    return or_(Score.stats.is_(None), cast(Score.stats, String) == "null")


def _first_stat_in_json(schema: list[dict]):
    """The first stat read out of the row's stats, the way `boards.stats_of` does: a row with no stats is a plain
    score, and a row that predates the schema may not have the stat at all (NULL = worst)."""
    key = schema[0]["key"]
    plain = Score.best if key == "score" else null()
    return case((_no_stats(), plain), else_=Score.stats[key].as_integer())


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


def _stat(db: Session, game_name: str, schema: list[dict], index: int):
    """Stat number `index` as the database sees it. The first stat may come from the indexed `best` column (see
    `_first_stat`); the others are read out of the row's stats, a missing one being NULL (= worst)."""
    if index == 0:
        return _first_stat(db, game_name, schema)
    key = schema[index]["key"]
    plain = Score.best if key == "score" else null()
    return case((_no_stats(), plain), else_=Score.stats[key].as_integer())


def _order(db: Session, game_name: str, schema: list[dict]) -> list:
    """The leaderboard order, in SQL: each stat in turn (a missing one last, then best first), then whoever got there
    first, then whoever was saved first. The same order `_sort_key` gives."""
    order = []
    for i, stat in enumerate(schema):
        value = _stat(db, game_name, schema, i)
        if value is not Score.best:  # `best` is never NULL; leaving the flag off lets the (game, best) index give the order
            order.append(case((value.is_(None), 1), else_=0))
        order.append(value.desc() if stat["order"] == "highest" else value.asc())
    return order + [Score.updated_at, Score.id]


def ranked(db: Session, game_name: str, limit: int, only_users: Iterable[int] | None = None) -> list[Score]:
    """The game's best runs, best first, in one query. The database walks the (game, best) index, so it only has to
    order the few players tied with the last one it returns."""
    schema = _schema(db, game_name)
    if schema is None:
        return []
    query = _filter(select(Score).options(joinedload(Score.user)), game_name, only_users)
    return list(db.scalars(query.order_by(*_order(db, game_name, schema)).limit(limit)).unique())


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


# --- outliers --------------------------------------------------------------------------------------------------

def _edge(db: Session, game_name: str, schema: list[dict]) -> int | None:
    """The 99th percentile of the first stat across the game's players, taken from the good end (the top 1% for a
    highest-is-best stat, the fastest 1% for a lowest-is-best one). None while the game has too few players to judge
    by. Kept for EDGE_SECONDS, so judging a run costs nothing most of the time."""
    key = (game_name, tuple((s["key"], s["order"]) for s in schema))
    hit = _edges.get(key)
    if hit is not None and clock() - hit[0] < EDGE_SECONDS:
        return hit[1]
    value = _first_stat(db, game_name, schema)
    players = db.scalar(select(func.count(Score.id)).where(Score.game_name == game_name, value.is_not(None)))
    edge = None
    if players >= settings.flag_min_players:
        good_first = value.desc() if schema[0]["order"] == "highest" else value.asc()
        edge = db.scalar(select(value).where(Score.game_name == game_name, value.is_not(None)).order_by(good_first).offset(int(players * 0.01)).limit(1))
    _edges[key] = (clock(), edge)
    return edge


def outlier_reason(db: Session, game_name: str, schema: list[dict], stats: dict, previous_first: int | None) -> str | None:
    """Why this run looks too good to be true, or None. Judged on the first stat only, in the direction that is better:
    more than `flag_previous_factor` times the player's previous best (when that was big enough to judge by), or more
    than `flag_percentile_factor` times the game's 99th percentile (when the game has enough players)."""
    first = schema[0]
    value = stats.get(first["key"])
    if value is None:
        return None
    highest = first["order"] == "highest"
    factor = settings.flag_previous_factor
    if previous_first is not None and previous_first >= settings.flag_min_previous:
        if (highest and value > previous_first * factor) or (not highest and value * factor < previous_first):
            return f"more than {factor}x their previous best ({previous_first:,} to {value:,})"
    factor = settings.flag_percentile_factor
    edge = _edge(db, game_name, schema)
    if edge is not None:
        if highest and value > edge * factor:
            return f"more than {factor}x the game's 99th percentile ({edge:,})"
        if not highest and edge > 0 and value * factor < edge:
            return f"under 1/{factor} of the game's 99th percentile ({edge:,})"
    return None


def hypothetical_rank(db: Session, game_name: str, schema: list[dict], first_value: int) -> int:
    """The place a first-stat value would take if it counted (players strictly better, plus one). Used to answer a
    flagged first run like any other, so nobody can tell it was held back."""
    value = _first_stat(db, game_name, schema)
    better = value > first_value if schema[0]["order"] == "highest" else value < first_value
    return db.scalar(select(func.count(Score.id)).where(Score.game_name == game_name, better)) + 1


def previous_top(db: Session, game_name: str) -> int | None:
    """Who is number one on the game's board right now, if anyone."""
    top = ranked(db, game_name, 1)
    return top[0].user_id if top else None


def add_flag(db: Session, user_id: int, game_name: str, stats: dict, previous_best: int | None, reason: str, run_id: int | None) -> ScoreFlag:
    flag = ScoreFlag(user_id=user_id, game_name=game_name, stats=stats, previous_best=previous_best, reason=reason[:200], run_id=run_id)
    db.add(flag)
    db.commit()
    return flag


def decide(db: Session, flag: ScoreFlag, outcome: str, admin_email: str, note: str | None) -> None:
    """Record an admin's decision. Cleared: the run counts from now on (the Progress Engine releases it, and it becomes the
    player's best if it beats their saved one). Confirmed: it stays out for good. The caller commits."""
    flag.status, flag.reviewed_by, flag.note = outcome, admin_email, note
    flag.reviewed_at = datetime.now(timezone.utc)
    if outcome != "cleared":
        return
    if flag.run_id is not None:
        progress.release_run(db, flag.run_id)
    schema = _schema(db, flag.game_name)
    if schema is None:
        return
    row = db.scalar(select(Score).where(Score.user_id == flag.user_id, Score.game_name == flag.game_name))
    first = flag.stats[schema[0]["key"]]
    if row is None:
        db.add(Score(user_id=flag.user_id, game_name=flag.game_name, best=first, stats=flag.stats))
    elif boards.is_better(schema, flag.stats, boards.stats_of(schema, row.stats, row.best)):
        row.best, row.stats = first, flag.stats
    db.flush()
    invalidate(flag.game_name)
