"""Game goals: a game lists them in its startng.json, and start.ng decides what each one pays from how hard it really
is, so no game can flood the economy and a hard game pays more than an easy one.

    {"rewards": {"goals": [
      {"key": "rich_driver", "label": "Earn 20,000 in one day", "stat": "naira", "at_least": 20000},
      {"key": "speedrun", "label": "Finish under 2 minutes", "stat": "time", "at_most": 120, "repeat": "daily"}
    ]}}

How hard a goal is = the share of the game's players whose best run meets it. Few players reaching it pays more.
Until a game has MIN_PLAYERS players there's too little to judge, and every goal pays the "new" amount.
"""
import logging
import re
import time

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import flags, manifest
from ... import leaderboard as boards
from ...models import Score
from .models import GameGoal

log = logging.getLogger(__name__)

MAX_GOALS = 10
MIN_PLAYERS = 20
NEW_GAME = ("new", 20)
# (at least this share of players reach it, tier, coins): the first that fits wins.
TIERS = [
    (0.50, "easy", 10),
    (0.20, "medium", 25),
    (0.05, "hard", 50),
    (0.01, "very_hard", 100),
    (0.00, "legendary", 150),
]
GAME_DAILY_CAP = 150  # coins a player can get from one game's goals in a day
CACHE_TTL = 600

_KEY = re.compile(r"^[a-z0-9_]{1,32}$")
_cache: dict[tuple[str, str], tuple[float, tuple[str, int, float]]] = {}


def met(goal: GameGoal, stats: dict) -> bool:
    value = stats.get(goal.stat)
    if not isinstance(value, int):
        return False
    return value >= goal.target if goal.at_least else value <= goal.target


def difficulty(db: Session, goal: GameGoal) -> tuple[str, int, float]:
    """(tier, coins, share of players who reach it). Daily goals pay half, at least 5. Kept for CACHE_TTL seconds."""
    hit = _cache.get((goal.game_name, goal.key))
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    rows = db.execute(select(Score.stats, Score.best).where(Score.game_name == goal.game_name)).all()
    players = len(rows)
    reached = sum(1 for stats, best in rows if met(goal, stats if stats is not None else {"score": best}))
    share = reached / players if players else 0.0
    if players < MIN_PLAYERS:
        tier, coins = NEW_GAME
    else:
        tier, coins = next((t, c) for floor, t, c in TIERS if share >= floor)
    if goal.daily:
        coins = max(5, coins // 2)
    out = (tier, coins, share)
    _cache[(goal.game_name, goal.key)] = (time.monotonic(), out)
    return out


def of_game(db: Session, game: str) -> list[GameGoal]:
    return list(db.scalars(select(GameGoal).where(GameGoal.game_name == game).order_by(GameGoal.id)))


def reset() -> None:
    _cache.clear()


# ---- the startng.json section

def parse(value):
    """Check the `rewards` section. A broken goal refuses the publish with a clear message, like a bad leaderboard."""
    from . import NAME
    if not flags.is_on(NAME):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("goals"), list):
        raise manifest.SchemaError('startng.json "rewards" needs a "goals" list')
    goals = value["goals"]
    if len(goals) > MAX_GOALS:
        raise manifest.SchemaError(f'startng.json "rewards" can have at most {MAX_GOALS} goals')
    out, seen = [], set()
    for i, g in enumerate(goals, start=1):
        where = f'rewards goal {i}'
        if not isinstance(g, dict):
            raise manifest.SchemaError(f"{where} must be an object")
        key, label, stat = g.get("key"), g.get("label"), g.get("stat")
        if not isinstance(key, str) or not _KEY.match(key) or key in seen:
            raise manifest.SchemaError(f'{where}: "key" must be unique, lowercase letters, digits and _')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise manifest.SchemaError(f'{where}: "label" must be 1 to 80 characters')
        if not isinstance(stat, str) or not _KEY.match(stat):
            raise manifest.SchemaError(f'{where}: "stat" must be one of your leaderboard keys')
        bounds = [b for b in ("at_least", "at_most") if b in g]
        if len(bounds) != 1 or not isinstance(g[bounds[0]], int) or isinstance(g[bounds[0]], bool) or not 0 <= g[bounds[0]] <= 1_000_000_000:
            raise manifest.SchemaError(f'{where}: give one of "at_least" or "at_most", a whole number from 0 to 1,000,000,000')
        repeat = g.get("repeat", "once")
        if repeat not in ("once", "daily"):
            raise manifest.SchemaError(f'{where}: "repeat" is "once" or "daily"')
        seen.add(key)
        # "coins" is ignored on purpose: start.ng sets what a goal pays from how hard it is.
        out.append({"key": key, "label": label.strip(), "stat": stat, "at_least": bounds[0] == "at_least",
                    "target": g[bounds[0]], "daily": repeat == "daily"})
    return out


def apply(db: Session, game, goals) -> None:
    """Save the game's goals when it goes live, replacing the old ones. Goals on a stat the game's leaderboard doesn't
    have are dropped (they could never be met)."""
    if goals is None:
        return
    keys = {s["key"] for s in (game.leaderboard or boards.DEFAULT)}
    db.execute(delete(GameGoal).where(GameGoal.game_name == game.name))
    for g in goals:
        if g["stat"] not in keys:
            log.info("Game %s: goal %s is on %s, which isn't on its leaderboard; skipped", game.name, g["key"], g["stat"])
            continue
        db.add(GameGoal(game_name=game.name, **g))
    for key in [k for k in _cache if k[0] == game.name]:
        del _cache[key]


manifest.register_section("rewards", parse, apply)


def players_of(db: Session, game: str) -> int:
    return db.scalar(select(func.count()).select_from(Score).where(Score.game_name == game)) or 0
