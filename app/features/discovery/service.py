"""Discovery (A1): how games are ranked and what the homepage shelves show.

Ranking (game_rank, contract C10) is how well a game is doing in the last 7 days: different people who opened it, scores per
player, and how many players came back on another day, times a penalty when the Kit says sessions are very short (a game
people quit in seconds sinks), times a boost for games under a week old. It is rewritten every 10 minutes.

Shelves: Featured and Staff picks (chosen by admins; Featured needs an eligible game), New this week (every game under 7
days old, whatever its rank, so beginners always show), Most played (by rank), Most improved (growth against the week
before) and Continue playing (the signed-in player's own games). A game owner can see exactly what stops it being featured.
"""
import importlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import flags, manifest, tournaments
from ...features.game_health.models import GameHealth
from ...features.progress.models import GameOpen, Run
from ...models import Game, GamePlay
from .models import FeaturedGame, GameDiscovery, GameRank

log = logging.getLogger(__name__)

NAME = "discovery"
WINDOW = timedelta(days=7)
NEW_FOR = timedelta(days=7)
STALE_AFTER = timedelta(minutes=30)
SHELF_SIZE = 8
SHORT_SESSION = 30  # seconds: a Kit game whose sessions average less than this is penalised
MIN_IMPROVED = 3  # people this week before a game can be "most improved"
BOOST = 1.5  # for games under a week old
SHELVES = ("featured", "staff")


class DiscoveryError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def visible(db: Session) -> list[Game]:
    return list(db.scalars(select(Game).where(Game.hidden.is_(None)).order_by(Game.created_at.desc())).all())


# ---- startng.json "mobile": true

def _parse_mobile(value) -> bool:
    if not isinstance(value, bool):
        raise manifest.SchemaError('mobile must be true or false ("mobile": true says the game works on a phone)')
    return value


def _save_mobile(db: Session, game, value: bool) -> None:
    row = db.get(GameDiscovery, game.name)
    if row is None:
        db.add(GameDiscovery(game_name=game.name, mobile=value))
    else:
        row.mobile = value


manifest.register_section("mobile", _parse_mobile, _save_mobile)

ALWAYS_MOBILE = {"hangout"}  # start.ng's own park: it has a touch joystick but no startng.json


def mobile_ready(db: Session) -> set[str]:
    """The games that say they work on a phone ("mobile": true in their startng.json), for the laptop and phone icons."""
    return set(db.scalars(select(GameDiscovery.game_name).where(GameDiscovery.mobile.is_(True)))) | ALWAYS_MOBILE


def is_mobile_ready(db: Session, name: str) -> bool:
    row = db.get(GameDiscovery, name)
    return name in ALWAYS_MOBILE or bool(row and row.mobile)


# ---- ranking

@dataclass
class Metrics:
    visitors_7: int = 0
    visitors_prev_7: int = 0
    runs_7: int = 0
    players_7: int = 0
    return_rate: float = 0.0


def session_seconds(db: Session, game: str, since: datetime) -> int | None:
    """Average session length when the Kit's telemetry (M1) is installed and the game has sessions, else None."""
    try:
        kit = importlib.import_module("app.features.kit.service")
        found = getattr(kit, "average_session_seconds")(db, game, since)
        return int(found) if found is not None else None
    except Exception:
        return None


def metrics(db: Session, at: datetime) -> dict[str, Metrics]:
    this_from, prev_from = at - WINDOW, at - 2 * WINDOW
    out: dict[str, Metrics] = {}
    seen_now: dict[str, set] = {}
    seen_prev: dict[str, set] = {}
    for game, visitor, when in db.execute(select(GamePlay.game_name, GamePlay.visitor, GamePlay.at).where(GamePlay.at >= prev_from, GamePlay.at <= at)):
        (seen_now if _utc(when) >= this_from else seen_prev).setdefault(game, set()).add(visitor)
    days: dict[str, dict[int, set]] = {}
    for game, user_id, when in db.execute(select(Run.game_name, Run.user_id, Run.created_at).where(Run.created_at >= this_from, Run.created_at <= at, Run.flagged.is_(False))):
        m = out.setdefault(game, Metrics())
        m.runs_7 += 1
        days.setdefault(game, {}).setdefault(user_id, set()).add(_utc(when).date())
    for game, players in days.items():
        out[game].players_7 = len(players)
        out[game].return_rate = sum(1 for d in players.values() if len(d) >= 2) / len(players)
    for game, v in seen_now.items():
        out.setdefault(game, Metrics()).visitors_7 = len(v)
    for game, v in seen_prev.items():
        out.setdefault(game, Metrics()).visitors_prev_7 = len(v)
    return out


def score_of(m: Metrics, avg_session: int | None, created_at: datetime, at: datetime) -> float:
    per_player = m.runs_7 / m.players_7 if m.players_7 else 0.0
    score = math.log1p(m.visitors_7) * 3 + math.log1p(per_player) * 2 + m.return_rate * 3
    if avg_session is not None:
        score *= min(1.0, max(0.1, avg_session / SHORT_SESSION))  # a 10 second session keeps a third of the score
    if at - _utc(created_at) < NEW_FOR:
        score *= BOOST
    return round(score, 4)


def recompute(db: Session, at: datetime | None = None) -> int:
    """Rewrite game_rank for every visible game. Returns how many rows."""
    at = at or now()
    found = metrics(db, at)
    games = visible(db)
    db.execute(delete(GameRank))
    for g in games:
        m = found.get(g.name, Metrics())
        session = session_seconds(db, g.name, at - WINDOW)
        db.add(GameRank(game_name=g.name, score=score_of(m, session, g.created_at, at), visitors_7=m.visitors_7, visitors_prev_7=m.visitors_prev_7,
                        runs_7=m.runs_7, players_7=m.players_7, return_rate=round(m.return_rate, 3), avg_session=session, computed_at=at))
    db.commit()
    return len(games)


def ensure_fresh(db: Session, at: datetime | None = None) -> None:
    at = at or now()
    newest = db.scalar(select(func.max(GameRank.computed_at)))
    if newest is None or at - _utc(newest) > STALE_AFTER:
        recompute(db, at)


def ranked(db: Session, limit: int = SHELF_SIZE, at: datetime | None = None) -> list[GameRank]:
    ensure_fresh(db, at)
    return list(db.scalars(select(GameRank).join(Game, Game.name == GameRank.game_name).where(Game.hidden.is_(None), GameRank.visitors_7 > 0)
                           .order_by(GameRank.score.desc(), GameRank.game_name).limit(limit)).all())


# ---- featuring

@dataclass
class Check:
    key: str
    label: str
    ok: bool
    required: bool = True


def eligibility(db: Session, game: Game) -> list[Check]:
    """What a game needs to be featured: every required check must pass; the Kit is preferred, not required."""
    health = db.get(GameHealth, game.name)
    discovery = db.get(GameDiscovery, game.name)
    return [
        Check("standards", "Meets the game standards (back link, start.ng name, share on X)", game.meets_standards is True),
        Check("cover", "Has a cover picture", bool(game.cover)),
        Check("description", "Has a description", bool(game.description)),
        Check("loads", "Loads (the health check passes)", health is None or health.status == "ok"),
        Check("leaderboard", 'Has a leaderboard ("leaderboard" in startng.json)', game.leaderboard is not None),
        Check("mobile", 'Says it works on a phone ("mobile": true in startng.json)', bool(discovery and discovery.mobile)),
        Check("kit", "Uses the start.ng Kit (preferred, not required)", bool(getattr(game, "uses_kit", False)), required=False),
    ]


def eligible_for_featuring(db: Session, game: Game) -> bool:
    return all(c.ok for c in eligibility(db, game) if c.required)


def on_shelf(db: Session, shelf: str) -> list[str]:
    rows = db.scalars(select(FeaturedGame).join(Game, Game.name == FeaturedGame.game_name).where(FeaturedGame.shelf == shelf, Game.hidden.is_(None))
                      .order_by(FeaturedGame.position, FeaturedGame.added_at)).all()
    return [r.game_name for r in rows]


def add(db: Session, shelf: str, game_name: str, by: str) -> None:
    if shelf not in SHELVES:
        raise DiscoveryError("bad_shelf", "The shelves are featured and staff", 404)
    game = db.get(Game, game_name)
    if game is None or game.hidden is not None:
        raise DiscoveryError("no_game", "There's no game with that name", 404)
    if shelf == "featured":
        missing = [c.label for c in eligibility(db, game) if c.required and not c.ok]
        if missing:
            raise DiscoveryError("not_eligible", "Not eligible to be featured yet: " + "; ".join(missing), 409)
    if db.get(FeaturedGame, (game_name, shelf)) is not None:
        return
    top = db.scalar(select(func.max(FeaturedGame.position)).where(FeaturedGame.shelf == shelf))
    db.add(FeaturedGame(game_name=game_name, shelf=shelf, position=(top if top is not None else -1) + 1, added_by=by, added_at=now()))
    db.commit()


def remove(db: Session, shelf: str, game_name: str) -> None:
    row = db.get(FeaturedGame, (game_name, shelf))
    if row is not None:
        db.delete(row)
        db.commit()


def move(db: Session, shelf: str, game_name: str, up: bool) -> None:
    rows = list(db.scalars(select(FeaturedGame).where(FeaturedGame.shelf == shelf).order_by(FeaturedGame.position, FeaturedGame.added_at)).all())
    i = next((n for n, r in enumerate(rows) if r.game_name == game_name), None)
    j = None if i is None else i - 1 if up else i + 1
    if i is None or j is None or not 0 <= j < len(rows):
        return
    rows[i], rows[j] = rows[j], rows[i]
    for n, r in enumerate(rows):
        r.position = n
    db.commit()


# ---- shelves

def new_this_week(db: Session, at: datetime | None = None) -> list[str]:
    at = at or now()
    return [g.name for g in visible(db) if at - _utc(g.created_at) < NEW_FOR][:12]


def most_improved(db: Session, at: datetime | None = None) -> list[str]:
    ensure_fresh(db, at)
    rows = db.scalars(select(GameRank).join(Game, Game.name == GameRank.game_name).where(
        Game.hidden.is_(None), GameRank.visitors_7 >= MIN_IMPROVED, GameRank.visitors_7 > GameRank.visitors_prev_7)).all()
    rows.sort(key=lambda r: (-(r.visitors_7 - r.visitors_prev_7) / (r.visitors_prev_7 + 3), r.game_name))
    return [r.game_name for r in rows[:SHELF_SIZE]]


def continue_playing(db: Session, user_id: int, at: datetime | None = None) -> list[str]:
    at = at or now()
    rows = db.scalars(select(GameOpen).join(Game, Game.name == GameOpen.game_name).where(
        GameOpen.user_id == user_id, Game.hidden.is_(None), GameOpen.last_at >= at - timedelta(days=30)).order_by(GameOpen.last_at.desc()).limit(SHELF_SIZE)).all()
    return [r.game_name for r in rows]


def shelves(db: Session, user_id: int | None, at: datetime | None = None) -> dict[str, list[str]]:
    return {
        "featured": on_shelf(db, "featured"),
        "staff": on_shelf(db, "staff"),
        "new": new_this_week(db, at),
        "most_played": [r.game_name for r in ranked(db, SHELF_SIZE, at)],
        "most_improved": most_improved(db, at),
        "continue_playing": continue_playing(db, user_id, at) if user_id else [],
    }


def _tournament_next(db: Session, after: str, user_id: int | None, limit: int, at: datetime | None) -> tuple[str, list[str]] | None:
    """When the game is in a tournament that is on now and that this player can see, the tournament's other games, in its order
    starting after this one, the ones they have not opened first: (the tournament's name, games). The tournaments that count are
    a private one they are a member of (only members may know its games), then today's public one. None when the game is in none
    of them, or is the only game in the one it is in (so the player still gets suggestions)."""
    at = at or tournaments.now()
    found: list[tuple[str, list[str]]] = []
    if user_id and flags.is_on("private_tournaments"):
        from ..private_tournaments.models import PrivateTournament, PrivateTournamentMember

        rows = db.scalars(
            select(PrivateTournament).join(PrivateTournamentMember, PrivateTournamentMember.tournament_id == PrivateTournament.id)
            .where(PrivateTournamentMember.user_id == user_id, PrivateTournament.starts_at <= at, PrivateTournament.ends_at > at)
            .order_by(PrivateTournament.ends_at)).all()
        found += [(t.title, [g.game_name for g in t.games]) for t in rows]
    today = tournaments.running(db, at)  # never makes one
    if today is not None:
        found.append(("today's tournament", [g.game_name for g in today.games]))
    opened = set(db.scalars(select(GameOpen.game_name).where(GameOpen.user_id == user_id)).all()) if user_id else set()
    for title, names in found:
        if after not in names:
            continue
        i = names.index(after)
        rotated = names[i + 1:] + names[:i]  # the ones after this one first, then back to the start
        visible = set(db.scalars(select(Game.name).where(Game.name.in_(rotated), Game.hidden.is_(None))).all())
        others = [n for n in rotated if n in visible]
        if others:
            return title, ([n for n in others if n not in opened] + [n for n in others if n in opened])[:limit]
    return None


def play_next_with_context(db: Session, after: str, user_id: int | None, limit: int = 3, at: datetime | None = None) -> tuple[list[str], str | None]:
    """Games to play after this one, and the tournament they come from when the game is in one that is on now. Inside a
    tournament only that tournament's games are offered; a game that is in none gets the best ranked games, as before."""
    in_tournament = _tournament_next(db, after, user_id, limit, at)
    if in_tournament is not None:
        return in_tournament[1], in_tournament[0]
    ensure_fresh(db, at)
    ranks = list(db.scalars(select(GameRank).join(Game, Game.name == GameRank.game_name).where(Game.hidden.is_(None), GameRank.game_name != after)
                            .order_by(GameRank.score.desc(), GameRank.game_name)).all())
    names = [r.game_name for r in ranks]
    opened = set(db.scalars(select(GameOpen.game_name).where(GameOpen.user_id == user_id)).all()) if user_id else set()
    fresh = [n for n in names if n not in opened]
    return (fresh + [n for n in names if n in opened])[:limit], None


def play_next(db: Session, after: str, user_id: int | None, limit: int = 3, at: datetime | None = None) -> list[str]:
    """One to three games to play after this one (contract C10): inside a tournament, that tournament's games; otherwise the best
    ranked, those the player hasn't opened first."""
    return play_next_with_context(db, after, user_id, limit, at)[0]
