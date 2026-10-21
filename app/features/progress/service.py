"""The Progress Engine (PRD D1). What other features call:

    from app.features.progress import service as progress

    progress.after_run(db, user_id, game, stats)   # called by the score route; logs the run, returns new awards
    progress.snapshot(db, user_id)                 # C2: runs, games, streaks, XP, level, tournament wins
    progress.register("wardrobe", [progress.Rule("item", "explorer_cap", lambda snap, run: snap.distinct_games >= 5)])

A rule is a function of the player's snapshot (after the run) and the run itself. The engine awards what a rule says,
once per player, and tells the score route (`unlocked`) and every subscriber (`award_unlocked`).
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events
from ...config import settings
from ...models import Score, Tournament, User
from .models import ActivityDay, Award, GameOpen, Run

log = logging.getLogger(__name__)

FIRST_RUN_XP = 10  # the first run of the day
RUN_XP = 2  # every run
DAILY_XP_CAP = 50
XP_PER_LEVEL_STEP = 50  # level n starts at XP_PER_LEVEL_STEP * (n - 1) ** 2
FREEZE_EVERY = 7  # a streak earns one freeze each time it reaches a multiple of this many days
MAX_FREEZES = 2
PURGE_EVERY = timedelta(hours=1)


def now() -> datetime:
    return datetime.now(timezone.utc)


def local_day(when: datetime) -> date:
    """The calendar day in Nigeria (the same offset the daily tournament uses)."""
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
    return when.astimezone(timezone(timedelta(hours=settings.tournament_utc_offset))).date()


def streak_state(played: Iterable[date], today: date) -> tuple[int, int, int]:
    """(current streak, best streak, freezes left). A streak is days played in a row. It earns a freeze every
    FREEZE_EVERY days (up to MAX_FREEZES); a missed day spends one automatically and the streak carries on (the frozen
    day itself adds nothing). With none left a missed day ends it. Today is never missed: it isn't over yet."""
    days = {d for d in played if d <= today}
    if not days:
        return 0, 0, 0
    streak = best = freezes = 0
    day = min(days)
    while day <= today:
        if day in days:
            streak += 1
            best = max(best, streak)
            if streak % FREEZE_EVERY == 0:
                freezes = min(MAX_FREEZES, freezes + 1)
        elif day != today and streak > 0:
            if freezes > 0:
                freezes -= 1
            else:
                streak = 0
        day += timedelta(days=1)
    return streak, best, freezes


def streaks(played: Iterable[date], today: date) -> tuple[int, int]:
    """(current, best) streak in days; see streak_state."""
    current, best, _ = streak_state(played, today)
    return current, best


def xp_for_run(runs_today: int, xp_today: int) -> int:
    gain = RUN_XP + (FIRST_RUN_XP if runs_today == 0 else 0)
    return max(0, min(gain, DAILY_XP_CAP - xp_today))


def level_for(xp: int) -> int:
    return math.isqrt(xp // XP_PER_LEVEL_STEP) + 1


@dataclass
class Snapshot:
    runs: int
    distinct_games: int
    current_streak: int
    best_streak: int
    streak_freezes: int  # freezes banked: each saves the streak from one missed day
    xp: int
    level: int
    tournament_wins: int
    ranks: dict = field(default_factory=dict)  # game -> place, only once held across the last 2 runs (the worse of the two)


def held_ranks(db: Session, user_id: int) -> dict[str, int]:
    """For each game, the player's place if they have held it across their last two runs there: the worse of the two.
    Flagged runs and runs from before ranks were recorded don't count."""
    rows = db.execute(
        select(Run.game_name, Run.rank_after).where(Run.user_id == user_id, Run.flagged.is_(False), Run.rank_after.is_not(None))
        .order_by(Run.created_at.desc(), Run.id.desc()).limit(500)
    ).all()
    last: dict[str, list[int]] = {}
    for game, rank in rows:
        if len(last.setdefault(game, [])) < 2:
            last[game].append(rank)
    return {game: max(ranks) for game, ranks in last.items() if len(ranks) == 2}


def snapshot(db: Session, user_id: int, today: date | None = None) -> Snapshot:
    today = today or local_day(now())
    runs, games = db.execute(
        select(func.count(Run.id), func.count(func.distinct(Run.game_name))).where(Run.user_id == user_id, Run.flagged.is_(False))
    ).one()
    played = db.scalars(select(ActivityDay.day).where(ActivityDay.user_id == user_id)).all()
    xp = db.scalar(select(func.coalesce(func.sum(ActivityDay.xp), 0)).where(ActivityDay.user_id == user_id))
    wins = db.scalar(select(func.count(Tournament.id)).where(Tournament.winner_id == user_id))
    current, best, freezes = streak_state(played, today)
    return Snapshot(runs=runs, distinct_games=games, current_streak=current, best_streak=best, streak_freezes=freezes,
                    xp=xp, level=level_for(xp), tournament_wins=wins, ranks=held_ranks(db, user_id))


@dataclass
class RunInfo:
    run_id: int
    user_id: int
    game: str
    stats: dict


@dataclass
class Rule:
    kind: str  # "item" or "badge"
    key: str
    check: Callable[[Snapshot, RunInfo], bool]


_rules: dict[str, list[Rule]] = {}


def register(feature: str, rules: list[Rule]) -> None:
    """A feature adds its award rules here (from its own rules.py). Registering again replaces that feature's rules."""
    _rules[feature] = list(rules)


@dataclass
class Result:
    run_id: int
    unlocked: list[dict] = field(default_factory=list)


def _log_run(db: Session, user_id: int, game: str, stats: dict, when: datetime, tournament_id: int | None = None,
             rank_after: int | None = None) -> Run:
    """Add the run and bump the player's day. Retried once when two first runs of a day race on the day's row."""
    for attempt in (1, 2):
        try:
            day = db.get(ActivityDay, (user_id, local_day(when)))
            if day is None:
                day = ActivityDay(user_id=user_id, day=local_day(when), runs=0, xp=0)
                db.add(day)
            day.xp += xp_for_run(day.runs, day.xp)
            day.runs += 1
            run = Run(user_id=user_id, game_name=game, score=next(iter(stats.values()), 0), stats=stats, created_at=when,
                      tournament_id=tournament_id, rank_after=rank_after)
            db.add(run)
            db.commit()
            return run
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _evaluate_rules(db: Session, user_id: int, info: "RunInfo") -> list[dict]:
    """Award what the registered rules say this run earned. Returns the new awards; nothing is published here."""
    snap = snapshot(db, user_id)
    owned = {(a.kind, a.key) for a in db.scalars(select(Award).where(Award.user_id == user_id))}
    unlocked: list[dict] = []
    for feature, rules in list(_rules.items()):
        for rule in rules:
            if (rule.kind, rule.key) in owned:
                continue
            try:
                earned = bool(rule.check(snap, info))
            except Exception:
                log.exception("Award rule %s/%s (%s) failed", rule.kind, rule.key, feature)
                continue
            if not earned:
                continue
            db.add(Award(user_id=user_id, kind=rule.kind, key=rule.key))
            try:
                db.commit()
            except IntegrityError:  # two runs at once earned it: the other one has it
                db.rollback()
                continue
            owned.add((rule.kind, rule.key))
            unlocked.append({"kind": rule.kind, "key": rule.key})
    return unlocked


def after_run(db: Session, user_id: int, game: str, stats: dict, tournament_id: int | None = None,
              flagged: bool = False, rank_after: int | None = None) -> Result:
    """Log this run (and the daily tournament it counted for, if any, and the player's place afterwards), update the
    player's day, evaluate every registered rule. Never raises because of a rule.
    A flagged run (score integrity holds it for review) is only logged: no XP, no day, no rules, no events."""
    if flagged:
        run = Run(user_id=user_id, game_name=game, score=next(iter(stats.values()), 0), stats=stats, created_at=now(), flagged=True)
        db.add(run)
        db.commit()
        return Result(run_id=run.id)
    run = _log_run(db, user_id, game, stats, now(), tournament_id, rank_after)
    maybe_purge(db)
    info = RunInfo(run_id=run.id, user_id=user_id, game=game, stats=stats)
    unlocked = _evaluate_rules(db, user_id, info)

    events.publish("run_recorded", {"user_id": user_id, "game": game, "run_id": run.id})
    for award in unlocked:
        events.publish("award_unlocked", {"user_id": user_id, "kind": award["kind"], "key": award["key"]})
    return Result(run_id=run.id, unlocked=unlocked)


def release_run(db: Session, run_id: int) -> list[dict]:
    """An admin cleared a flagged run: it counts from now on (runs, games, ranks) and the award rules see it. Its XP is
    not added back (the day it was played has moved on). Returns the awards it earned."""
    run = db.get(Run, run_id)
    if run is None or not run.flagged:
        return []
    run.flagged = False
    db.commit()
    unlocked = _evaluate_rules(db, run.user_id, RunInfo(run_id=run.id, user_id=run.user_id, game=run.game_name, stats=run.stats))
    events.publish("run_recorded", {"user_id": run.user_id, "game": run.game_name, "run_id": run.id})
    for award in unlocked:
        events.publish("award_unlocked", {"user_id": run.user_id, "kind": award["kind"], "key": award["key"]})
    return unlocked


def awards_of(db: Session, user_id: int) -> list[Award]:
    return list(db.scalars(select(Award).where(Award.user_id == user_id).order_by(Award.earned_at.desc(), Award.id.desc())))


# --- game opens (C4) -------------------------------------------------------------------------------------------

def record_open(db: Session, user_id: int, game: str) -> None:
    """A signed-in player opened a game: remember the first time, the last time and how many times."""
    when = now()
    for attempt in (1, 2):
        row = db.get(GameOpen, (user_id, game))
        if row is None:
            db.add(GameOpen(user_id=user_id, game_name=game, first_at=when, last_at=when, count=1))
        else:
            row.last_at, row.count = when, row.count + 1
        try:
            db.commit()
            return
        except IntegrityError:  # two first opens at once: count it on the one that won
            db.rollback()
            if attempt == 2:
                raise


def game_open(db: Session, user_id: int, game: str) -> GameOpen | None:
    return db.get(GameOpen, (user_id, game))


# --- backfill --------------------------------------------------------------------------------------------------

def backfill_from_scores(db: Session, batch: int = 1000) -> int:
    """Give players who scored before the Progress Engine existed one run per game they have a score in, so "tried 5
    games" works for them. Safe to run again: a player/game that already has a run is skipped. It makes no daily
    activity (the real days are unknown), so it gives no streak and no XP."""
    already = exists().where(Run.user_id == Score.user_id, Run.game_name == Score.game_name)
    made = 0
    while True:
        rows = db.scalars(select(Score).where(~already).order_by(Score.id).limit(batch)).all()
        if not rows:
            return made
        for s in rows:
            db.add(Run(user_id=s.user_id, game_name=s.game_name, score=s.best, stats=s.stats or {"score": s.best}, created_at=s.updated_at))
        db.commit()
        made += len(rows)


# --- retention -------------------------------------------------------------------------------------------------

_last_purge: datetime | None = None


def purge_old_runs(db: Session) -> int:
    """Remove runs older than runs_retention_days. Daily activity, streaks, XP and awards are kept."""
    cutoff = now() - timedelta(days=settings.runs_retention_days)
    removed = db.execute(delete(Run).where(Run.created_at < cutoff)).rowcount
    db.commit()
    return removed


def maybe_purge(db: Session) -> int:
    """purge_old_runs, but at most once an hour per server process, so a busy game doesn't pay for it on every run."""
    global _last_purge
    when = now()
    if _last_purge is not None and when - _last_purge < PURGE_EVERY:
        return 0
    _last_purge = when
    return purge_old_runs(db)


# --- who may see a player's progress ---------------------------------------------------------------------------

_visibility: dict[str, Callable[[Session, int], bool]] = {}


def register_visibility(feature: str, can_see: Callable[[Session, int], bool]) -> None:
    """A feature (the player profile, S1) can hide a player's public progress: `can_see(db, user_id)` is False when
    that player's page is private."""
    _visibility[feature] = can_see


def public_snapshot(db: Session, user_id: int) -> Snapshot | None:
    """The player's snapshot for anyone to see, or None when there is no such player or they keep it private."""
    if db.get(User, user_id) is None or not all(check(db, user_id) for check in list(_visibility.values())):
        return None
    return snapshot(db, user_id)
