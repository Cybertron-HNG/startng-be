"""The creator hub (T2): numbers for creators, from the raw plays (kept 30 days) and the run log (180 days), rolled up each
night into game_daily_stats so history outlives the raw rows. Everything here is aggregate: players are never identified.

Days are Nigeria's, like the daily tournament's. Average session length comes from the Kit (M1) when it is installed: it
publishes `average_session_seconds(db, game, since)` in app.features.kit.service, and until then the dashboard says
"unknown" (it works for a creator with no Kit data).
"""
import importlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...features.progress.models import Run
from ...models import Game, GamePlay, User
from ...routers.games import _player_name
from ...routers.pages import _title, slugify
from .models import CreatorProfile, GameDailyStat

log = logging.getLogger(__name__)

NAME = "creator_hub"
RAW_PLAYS_DAYS = 30  # app/routers/games.py PLAYS_KEPT_DAYS: the raw plays that are left
RUN_DAYS = 180  # the run log's retention
LEADERBOARD_SIZE = 20
MAX_RUNS_READ = 50_000  # repeat players are counted from at most this many runs


def now() -> datetime:
    return datetime.now(timezone.utc)


def _zone() -> timezone:
    return timezone(timedelta(hours=settings.tournament_utc_offset))


def local_day(when: datetime) -> date:
    when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
    return when.astimezone(_zone()).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC moments a Nigeria day starts and ends."""
    start = datetime(day.year, day.month, day.day, tzinfo=_zone())
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


# ---- counting from the raw rows

def count_days(db: Session, first: date, last: date, games: list[str] | None = None) -> dict[tuple[str, date], dict[str, int]]:
    """Plays, visitors, runs and players per game per day, from the raw plays and run log, for first..last (inclusive)."""
    out: dict[tuple[str, date], dict[str, int]] = {}
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    plays = select(GamePlay.game_name, GamePlay.visitor, GamePlay.at).where(GamePlay.at >= start, GamePlay.at < end)
    runs = select(Run.game_name, Run.user_id, Run.created_at).where(Run.created_at >= start, Run.created_at < end, Run.flagged.is_(False))
    if games is not None:
        plays, runs = plays.where(GamePlay.game_name.in_(games)), runs.where(Run.game_name.in_(games))
    visitors: dict[tuple[str, date], set] = {}
    players: dict[tuple[str, date], set] = {}
    for game, visitor, at in db.execute(plays):
        key = (game, local_day(at))
        out.setdefault(key, {"plays": 0, "visitors": 0, "runs": 0, "players": 0})["plays"] += 1
        visitors.setdefault(key, set()).add(visitor)
    for game, user_id, at in db.execute(runs):
        key = (game, local_day(at))
        out.setdefault(key, {"plays": 0, "visitors": 0, "runs": 0, "players": 0})["runs"] += 1
        players.setdefault(key, set()).add(user_id)
    for key, row in out.items():
        row["visitors"], row["players"] = len(visitors.get(key, ())), len(players.get(key, ()))
    return out


# ---- the nightly rollup

def rolled_through(db: Session) -> date | None:
    return db.scalar(select(func.max(GameDailyStat.day)))


def rollup(db: Session, first: date, last: date) -> int:
    """Write (or rewrite) the daily rows for first..last: one per game per day, zeros included, so "rolled up through" is
    the last day written even when nobody played. Safe to repeat. Returns how many rows were written."""
    counted = count_days(db, first, last)
    names = list(db.scalars(select(Game.name)).all())
    db.query(GameDailyStat).filter(GameDailyStat.day >= first, GameDailyStat.day <= last).delete()
    written = 0
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        for game in names:
            db.add(GameDailyStat(game_name=game, day=day, **counted.get((game, day), {"plays": 0, "visitors": 0, "runs": 0, "players": 0})))
            written += 1
    db.commit()
    return written


def rollup_missing(db: Session, today: date | None = None) -> int:
    """Roll up every finished day that isn't yet: from the day after the last one rolled (or from the oldest raw row when
    nothing was rolled yet) to yesterday. Called by the background loop; the days of an outage are filled in afterwards."""
    today = today or local_day(now())
    yesterday = today - timedelta(days=1)
    last = rolled_through(db)
    if last is None:
        oldest = min((d for d in (db.scalar(select(func.min(GamePlay.at))), db.scalar(select(func.min(Run.created_at)))) if d is not None), default=None)
        if oldest is None:
            return 0
        first = max(local_day(oldest), today - timedelta(days=RUN_DAYS))
    else:
        first = last + timedelta(days=1)
    if first > yesterday:
        return 0
    return rollup(db, first, yesterday)


# ---- series and totals

def daily(db: Session, games: list[str], days: int, today: date | None = None) -> dict[str, list[dict]]:
    """For each game, one entry per day for the last `days` days ending today: the rolled-up numbers, and the days after
    the last rollup (today, or yesterday if the job is behind) counted live from the raw rows."""
    today = today or local_day(now())
    first = today - timedelta(days=days - 1)
    through = rolled_through(db)
    rows = {(r.game_name, r.day): r for r in db.scalars(select(GameDailyStat).where(
        GameDailyStat.game_name.in_(games), GameDailyStat.day >= first, GameDailyStat.day <= today)).all()} if games else {}
    live_from = max(first, through + timedelta(days=1)) if through is not None else first
    live = count_days(db, live_from, today, games) if games and live_from <= today else {}
    out: dict[str, list[dict]] = {g: [] for g in games}
    for i in range(days):
        day = first + timedelta(days=i)
        for g in games:
            if day >= live_from:
                n = live.get((g, day), {})
            else:
                r = rows.get((g, day))
                n = {"plays": r.plays, "visitors": r.visitors, "runs": r.runs, "players": r.players} if r else {}
            out[g].append({"day": day, "plays": n.get("plays", 0), "visitors": n.get("visitors", 0), "runs": n.get("runs", 0), "players": n.get("players", 0)})
    return out


def total_plays(db: Session, games: list[str], today: date | None = None) -> dict[str, int]:
    """All-time plays per game: the rolled-up days plus the days not yet rolled up."""
    today = today or local_day(now())
    if not games:
        return {}
    totals = dict(db.execute(select(GameDailyStat.game_name, func.coalesce(func.sum(GameDailyStat.plays), 0))
                             .where(GameDailyStat.game_name.in_(games)).group_by(GameDailyStat.game_name)).all())
    through = rolled_through(db)
    first = (through + timedelta(days=1)) if through is not None else today - timedelta(days=RAW_PLAYS_DAYS)
    if first <= today:
        for (game, _day), n in count_days(db, first, today, games).items():
            totals[game] = totals.get(game, 0) + n["plays"]
    return {g: int(totals.get(g, 0)) for g in games}


def unique_visitors(db: Session, games: list[str], days: int = 30, today: date | None = None) -> int:
    """Different people who opened any of these games in the last `days` days (from the raw plays)."""
    today = today or local_day(now())
    if not games:
        return 0
    start, _ = day_bounds(today - timedelta(days=days - 1))
    return db.scalar(select(func.count(func.distinct(GamePlay.visitor))).where(GamePlay.game_name.in_(games), GamePlay.at >= start)) or 0


def repeat_players(db: Session, game: str, days: int = 30, today: date | None = None) -> int:
    """Signed-in players who sent a score on at least two different days in the window."""
    today = today or local_day(now())
    start, _ = day_bounds(today - timedelta(days=days - 1))
    rows = db.execute(select(Run.user_id, Run.created_at).where(Run.game_name == game, Run.created_at >= start, Run.flagged.is_(False))
                      .limit(MAX_RUNS_READ)).all()
    seen: dict[int, set[date]] = {}
    for user_id, at in rows:
        seen.setdefault(user_id, set()).add(local_day(at))
    return sum(1 for d in seen.values() if len(d) >= 2)


def session_seconds(db: Session, game: str, since: datetime) -> int | None:
    """Average session length, when the Kit's telemetry (M1) is installed and the game has sessions; otherwise None."""
    try:
        kit = importlib.import_module("app.features.kit.service")
        found = getattr(kit, "average_session_seconds")(db, game, since)
        return int(found) if found is not None else None
    except Exception:  # no Kit yet, or no sessions: the dashboard just says unknown
        return None


# ---- creators

def visible_games(db: Session, user_id: int) -> list[Game]:
    return list(db.scalars(select(Game).where(Game.owner_id == user_id, Game.hidden.is_(None)).order_by(Game.created_at.desc())).all())


def slug_for(user: User) -> str:
    name = slugify(_player_name(user))
    return f"{user.id}-{name}" if name else str(user.id)


def bio_of(db: Session, user_id: int) -> str | None:
    row = db.get(CreatorProfile, user_id)
    return row.bio if row else None


def set_bio(db: Session, user_id: int, bio: str | None) -> str | None:
    text = " ".join((bio or "").split())[:200] or None
    row = db.get(CreatorProfile, user_id)
    if row is None:
        row = CreatorProfile(user_id=user_id)
        db.add(row)
    row.bio = text
    db.commit()
    return text


@dataclass
class Leader:
    user: User
    games: int
    players: int  # different people who opened any of their games in the last 30 days
    plays: int


def leaderboard(db: Session, today: date | None = None, limit: int = LEADERBOARD_SIZE) -> list[Leader]:
    """Top creators by unique players in the last 30 days (their visible games only)."""
    today = today or local_day(now())
    start, _ = day_bounds(today - timedelta(days=RAW_PLAYS_DAYS - 1))
    rows = db.execute(
        select(Game.owner_id, func.count(func.distinct(GamePlay.visitor)), func.count(GamePlay.id), func.count(func.distinct(GamePlay.game_name)))
        .join(Game, Game.name == GamePlay.game_name).where(GamePlay.at >= start, Game.hidden.is_(None))
        .group_by(Game.owner_id).order_by(func.count(func.distinct(GamePlay.visitor)).desc(), Game.owner_id).limit(limit)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([r[0] for r in rows]))).all()}
    count = dict(db.execute(select(Game.owner_id, func.count()).where(Game.hidden.is_(None), Game.owner_id.in_(list(users))).group_by(Game.owner_id)).all())
    return [Leader(user=users[o], games=count.get(o, 0), players=p, plays=pl) for o, p, pl, _g in rows if o in users]


@dataclass
class GameStats:
    game: str
    title: str
    hidden: str | None
    plays_7: int
    plays_30: int
    plays_all: int
    visitors_30: int
    players_30: int
    repeat_players_30: int
    runs_30: int
    avg_session_seconds: int | None
    change_7: int  # plays in the last 7 days minus the 7 before
    trend: list[dict] = field(default_factory=list)  # [{day, plays}] for the last 30 days


@dataclass
class Dashboard:
    games: list[GameStats]
    best_game: str | None
    plays_7: int
    plays_30: int
    visitors_30: int


def dashboard(db: Session, user_id: int, today: date | None = None) -> Dashboard:
    """A creator's own numbers, for every game they own (hidden ones too, marked)."""
    today = today or local_day(now())
    mine = list(db.scalars(select(Game).where(Game.owner_id == user_id).order_by(Game.created_at.desc())).all())
    names = [g.name for g in mine]
    series = daily(db, names, 30, today)
    totals = total_plays(db, names, today)
    since, _ = day_bounds(today - timedelta(days=29))
    out = []
    for g in mine:
        s = series[g.name]
        p30, p7, prev7 = sum(d["plays"] for d in s), sum(d["plays"] for d in s[-7:]), sum(d["plays"] for d in s[-14:-7])
        out.append(GameStats(
            game=g.name, title=_title(g.name), hidden=g.hidden, plays_7=p7, plays_30=p30, plays_all=totals.get(g.name, 0),
            visitors_30=unique_visitors(db, [g.name], 30, today),
            players_30=db.scalar(select(func.count(func.distinct(Run.user_id))).where(Run.game_name == g.name, Run.created_at >= since, Run.flagged.is_(False))) or 0,
            repeat_players_30=repeat_players(db, g.name, 30, today), runs_30=sum(d["runs"] for d in s),
            avg_session_seconds=session_seconds(db, g.name, since), change_7=p7 - prev7,
            trend=[{"day": d["day"], "plays": d["plays"]} for d in s]))
    best = max(out, key=lambda x: (x.plays_30, x.plays_all), default=None)
    return Dashboard(games=out, best_game=best.game if best and best.plays_30 > 0 else None, plays_7=sum(x.plays_7 for x in out),
                     plays_30=sum(x.plays_30 for x in out), visitors_30=unique_visitors(db, names, 30, today))
