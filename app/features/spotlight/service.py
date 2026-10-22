"""Spotlight (A2): Game of the Week and the daily challenge.

Game of the Week is an editorial pick for a week (Monday to Sunday, Nigeria time), chosen in the admin Spotlight tab. Other
features read it with `game_of_the_week(db)` (the weekly digest, H1).

The daily challenge is one personal goal a day on one game, made the first time anyone asks for it that day: the game is
picked from those with enough recent runs, and the goal is the value about 40% of the game's recent runs reached (about the
60th percentile), so it is reachable by construction. It resets at 00:00 Nigeria time, uses only runs (never the daily
tournament's entries), and a player completes it once a day. Completing it earns the "Daily challenger" badge through the
Progress Engine (D1).
"""
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events, flags
from ... import leaderboard as boards
from ...config import settings
from ...db import SessionLocal
from ...features.progress import service as progress
from ...features.progress.models import Run
from ...models import Game
from .models import ChallengeCompletion, DailyChallenge, GameOfTheWeek

log = logging.getLogger(__name__)

NAME = "spotlight"
LOOK_BACK = timedelta(days=14)
MIN_RUNS = 10  # a game needs this many recent runs (from MIN_PLAYERS people) to be a daily challenge
MIN_PLAYERS = 3
SHARE_REACHING = 0.4  # about this share of recent runs reached the goal


class SpotlightError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def _zone() -> timezone:
    return timezone(timedelta(hours=settings.tournament_utc_offset))


def local_day(when: datetime) -> date:
    return _utc(when).astimezone(_zone()).date()


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=_zone()).astimezone(timezone.utc)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


# ---- Game of the Week

def game_of_the_week(db: Session, at: datetime | None = None) -> GameOfTheWeek | None:
    """This week's pick, if there is one and the game is still live."""
    row = db.get(GameOfTheWeek, week_start(local_day(at or now())))
    game = db.get(Game, row.game_name) if row else None
    return row if row is not None and game is not None and game.hidden is None else None


def pick_game_of_the_week(db: Session, game_name: str, by: str, note: str | None = None, week: date | None = None, at: datetime | None = None) -> GameOfTheWeek:
    """Choose (or replace) the pick for a week (this one by default; any Monday, this week or later)."""
    game = db.get(Game, game_name)
    if game is None or game.hidden is not None:
        raise SpotlightError("no_game", "There's no live game with that name", 404)
    this_week = week_start(local_day(at or now()))
    week = week_start(week) if week else this_week
    if week < this_week:
        raise SpotlightError("past_week", "That week is over")
    row = db.get(GameOfTheWeek, week)
    if row is None:
        row = GameOfTheWeek(week_start=week, game_name=game_name, picked_by=by)
        db.add(row)
    row.game_name, row.picked_by, row.picked_at = game_name, by, now()
    row.note = " ".join((note or "").split())[:300] or None
    db.commit()
    return row


def clear_game_of_the_week(db: Session, week: date) -> None:
    row = db.get(GameOfTheWeek, week_start(week))
    if row is not None:
        db.delete(row)
        db.commit()


# ---- the daily challenge

@dataclass
class Goal:
    game: str
    stat_key: str
    goal: int
    runs_seen: int


def meets(schema: list[dict], stat_key: str, goal: int, stats: dict) -> bool:
    stat = next((s for s in schema if s["key"] == stat_key), None)
    value = stats.get(stat_key) if stat else None
    if value is None:
        return False
    return value >= goal if stat["order"] == "highest" else value <= goal


def _goal_for(db: Session, game: Game, at: datetime) -> Goal | None:
    schema = game.leaderboard or boards.DEFAULT
    stat = schema[0]
    rows = db.execute(select(Run.user_id, Run.stats).where(Run.game_name == game.name, Run.flagged.is_(False), Run.created_at >= at - LOOK_BACK,
                                                           Run.created_at <= at)).all()
    values = sorted(s.get(stat["key"]) for _u, s in rows if isinstance(s.get(stat["key"]), int))
    if len(values) < MIN_RUNS or len({u for u, _s in rows}) < MIN_PLAYERS:
        return None
    # At least SHARE_REACHING of the runs reached it: the value that many of the best runs share. (Values are sorted low to
    # high, so for "highest" that is counted from the top, for "lowest", a time, from the bottom.)
    reaching = math.ceil(len(values) * SHARE_REACHING)
    goal = values[len(values) - reaching] if stat["order"] == "highest" else values[reaching - 1]
    return Goal(game=game.name, stat_key=stat["key"], goal=goal, runs_seen=len(values))


def challenge_for(db: Session, day: date, at: datetime | None = None) -> DailyChallenge | None:
    """The day's challenge, made now if nobody has asked yet. None when no game has enough recent play."""
    row = db.get(DailyChallenge, day)
    if row is not None:
        return row
    at = at or now()
    yesterday = db.get(DailyChallenge, day - timedelta(days=1))
    games = [g for g in db.scalars(select(Game).where(Game.hidden.is_(None)).order_by(Game.name)).all()]
    candidates = [(g, goal) for g in games if (goal := _goal_for(db, g, at)) is not None]
    if not candidates:
        return None
    if len(candidates) > 1 and yesterday is not None:
        candidates = [c for c in candidates if c[0].name != yesterday.game_name] or candidates
    game, goal = candidates[day.toordinal() % len(candidates)]
    row = DailyChallenge(day=day, game_name=game.name, stat_key=goal.stat_key, goal=goal.goal, runs_seen=goal.runs_seen)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # another request made it first
        db.rollback()
        row = db.get(DailyChallenge, day)
    return row


def today(db: Session, at: datetime | None = None) -> DailyChallenge | None:
    at = at or now()
    return challenge_for(db, local_day(at), at)


def completed(db: Session, day: date, user_id: int) -> ChallengeCompletion | None:
    return db.get(ChallengeCompletion, (day, user_id))


def best_today(db: Session, challenge: DailyChallenge, user_id: int) -> int | None:
    """The player's best value on the challenge's game since the day began (their own runs, nobody else's)."""
    game = db.get(Game, challenge.game_name)
    schema = game.leaderboard or boards.DEFAULT
    stat = next((s for s in schema if s["key"] == challenge.stat_key), schema[0])
    values = [s.get(challenge.stat_key) for (s,) in db.execute(select(Run.stats).where(
        Run.user_id == user_id, Run.game_name == challenge.game_name, Run.flagged.is_(False), Run.created_at >= day_start(challenge.day)))]
    values = [v for v in values if isinstance(v, int)]
    return (max(values) if stat["order"] == "highest" else min(values)) if values else None


def record_run(db: Session, user_id: int, run: Run, at: datetime | None = None) -> bool:
    """If this run reaches today's goal and the player hasn't completed it yet, record the completion."""
    at = at or now()
    c = db.get(DailyChallenge, local_day(at))  # only a challenge somebody already asked for: a run never makes one
    if c is None or run.flagged or run.game_name != c.game_name or _utc(run.created_at) < day_start(c.day):
        return False
    game = db.get(Game, c.game_name)
    if game is None or not meets(game.leaderboard or boards.DEFAULT, c.stat_key, c.goal, run.stats):
        return False
    if completed(db, c.day, user_id) is not None:
        return False
    db.add(ChallengeCompletion(day=c.day, user_id=user_id, run_id=run.id, value=run.stats[c.stat_key], completed_at=at))
    try:
        db.commit()
    except IntegrityError:  # two runs at once: the other one completed it
        db.rollback()
        return False
    return True


def sync(db: Session, user_id: int, challenge: DailyChallenge, at: datetime | None = None) -> bool:
    """A player who played the game earlier today, before looking at the challenge: if their best run today reaches the
    goal, it counts. (Only runs since 00:00 Nigeria time, only their own.)"""
    if completed(db, challenge.day, user_id) is not None:
        return False
    best = best_today(db, challenge, user_id)
    game = db.get(Game, challenge.game_name)
    if best is None or game is None or not meets(game.leaderboard or boards.DEFAULT, challenge.stat_key, challenge.goal, {challenge.stat_key: best}):
        return False
    db.add(ChallengeCompletion(day=challenge.day, user_id=user_id, run_id=None, value=best, completed_at=at or now()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


@events.subscribe("run_recorded")
def on_run_recorded(payload: dict) -> None:
    if not flags.is_on(NAME):
        return
    with SessionLocal() as db:
        run = db.get(Run, payload["run_id"])
        if run is not None:
            record_run(db, payload["user_id"], run)


# ---- the Progress Engine's badge: completing a daily challenge

def _completes_today(snap, info) -> bool:
    """A D1 rule: this run reaches today's goal. (Rules run before run_recorded, so it can't wait for the completion row.)"""
    if not flags.is_on(NAME):
        return False
    with SessionLocal() as db:
        c = db.get(DailyChallenge, local_day(now()))
        game = db.get(Game, c.game_name) if c else None
        return bool(c and info.game == c.game_name and game and meets(game.leaderboard or boards.DEFAULT, c.stat_key, c.goal, info.stats))


BADGES = {"daily_challenger": "Completed a daily challenge"}
progress.register(NAME, [progress.Rule("badge", "daily_challenger", _completes_today)])
