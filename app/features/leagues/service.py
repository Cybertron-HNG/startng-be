"""Leagues (PRD M2), the Duolingo model. What other features may call:

    from app.features.leagues import service as leagues

    leagues.join(db, user_id)                 # seat a player in every track's current round (runs do this for you)
    leagues.standings(db, "daily", user_id)   # their group's table right now, with the up and down zones
    leagues.history(db, "weekly", user_id)    # their settled rounds, newest first

Three tracks run all the time: Sprint, Daily and Weekly (settings.league_*_hours). Rounds are fixed blocks counted
from a Monday at the daily tournament's reset hour, so they line up with it, and a 2-hour round across midnight is
just another block. Each track has its own tiers, groups and history.

Joining: a player's first unflagged run in a round (the `run_recorded` event) seats them in that round in every
track, in the first group of their tier with space (GROUP_SIZE). So every group is only people who played.

Points: per game, each player's best run this round (the game's own order, so "lowest wins" games work), scored as
a percentile among everyone in the round who played that game: 100 x (players beaten + half the ties) / (players - 1),
or 100 when they played alone. A player's best BEST_GAMES games are added up and rounded. Flagged runs never count.

Settling: nothing runs on a timer. A round that has ended is settled the next time anyone reads the track, once
(a single update claims it). Groups under MIN_TABLE players are combined within the tier, then with the tier below;
a table still under MIN_TABLE moves nobody ("waiting for players"). Otherwise the top MOVE go up and the bottom MOVE
go down. A player whose runs were all flagged afterwards is "out". Missing MISSED_TO_DROP or more rounds in a row
drops a player one tier when they come back. Sprint sends no events (PRD: no rewards from Sprint).
"""
import logging
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events, flags
from ... import leaderboard as boards
from ...config import settings
from ...db import SessionLocal
from ...models import Game, User
from ..progress.models import Run  # read only: the run log belongs to the Progress Engine (D1)
from .models import LeagueEntry, LeagueGroup, LeagueMember, LeagueRound

log = logging.getLogger(__name__)

FLAG = "leagues"
TRACKS = ("sprint", "daily", "weekly")
REWARD_TRACKS = {"daily", "weekly"}  # Sprint is the easiest to farm, so it publishes no events
TIERS = ("bronze", "silver", "gold", "diamond")
GROUP_SIZE = 30
MIN_TABLE = 10  # fewer active players than this: combined with others, or nobody moves
MOVE = 5  # top this many go up, bottom this many go down
BEST_GAMES = 5
MISSED_TO_DROP = 2
HISTORY_LIMIT = 20


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(when: datetime) -> datetime:
    """SQLite drops the zone; everything stored here is UTC."""
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


# ---- round times


def length(track: str) -> timedelta:
    hours = {"sprint": settings.league_sprint_hours, "daily": settings.league_daily_hours,
             "weekly": settings.league_weekly_hours}[track]
    return timedelta(hours=hours)


def _anchor() -> datetime:
    """Monday 1 Jan 2024 at the daily tournament's reset hour, local time: every track's blocks count from here."""
    local = timezone(timedelta(hours=settings.tournament_utc_offset))
    return datetime(2024, 1, 1, settings.tournament_end_hour, tzinfo=local).astimezone(timezone.utc)


def window(track: str, at: datetime) -> tuple[datetime, datetime]:
    """The start and end of the track's round running at `at`."""
    size = length(track)
    start = _anchor() + ((utc(at) - _anchor()) // size) * size
    return start, start + size


def round_at(db: Session, track: str, at: datetime, create: bool = True) -> LeagueRound | None:
    """The track's round running at `at`, made now if it doesn't exist yet (unless create is False)."""
    start, end = window(track, at)
    found = db.scalar(select(LeagueRound).where(LeagueRound.track == track, LeagueRound.starts_at == start))
    if found is not None or not create:
        return found
    db.add(LeagueRound(track=track, starts_at=start, ends_at=end))
    try:
        db.commit()
    except IntegrityError:  # another request made it first: use theirs
        db.rollback()
    return db.scalar(select(LeagueRound).where(LeagueRound.track == track, LeagueRound.starts_at == start))


# ---- joining


def _free_seat(db: Session, rnd: LeagueRound, tier: int) -> tuple[LeagueGroup, int]:
    """The first group of this tier with space, and the next seat in it (a new group when they're all full)."""
    groups = db.scalars(select(LeagueGroup).where(LeagueGroup.round_id == rnd.id, LeagueGroup.tier == tier)
                        .order_by(LeagueGroup.number)).all()
    taken = dict(db.execute(select(LeagueEntry.group_id, func.count()).where(LeagueEntry.round_id == rnd.id)
                            .group_by(LeagueEntry.group_id)).all())
    for group in groups:
        if taken.get(group.id, 0) < GROUP_SIZE:
            return group, taken.get(group.id, 0) + 1
    group = LeagueGroup(round_id=rnd.id, tier=tier, number=(groups[-1].number + 1) if groups else 1)
    db.add(group)
    db.flush()
    return group, 1


def _seat(db: Session, rnd: LeagueRound, user_id: int) -> None:
    """Seat a player in a round (once). Retried when another player takes the same seat at the same moment."""
    for _ in range(10):
        try:
            if db.scalar(select(LeagueEntry.id).where(LeagueEntry.round_id == rnd.id, LeagueEntry.user_id == user_id)):
                return
            member = db.scalar(select(LeagueMember).where(LeagueMember.user_id == user_id, LeagueMember.track == rnd.track))
            if member is None:
                member = LeagueMember(user_id=user_id, track=rnd.track, tier=0)
                db.add(member)
            elif member.last_round_start is not None:
                missed = (utc(rnd.starts_at) - utc(member.last_round_start)) // length(rnd.track) - 1
                if missed >= MISSED_TO_DROP and member.tier > 0:
                    member.tier -= 1
            member.last_round_start = rnd.starts_at
            group, seat = _free_seat(db, rnd, member.tier)
            db.add(LeagueEntry(round_id=rnd.id, group_id=group.id, user_id=user_id, seat=seat, tier=member.tier))
            db.commit()
            return
        except IntegrityError:
            db.rollback()
    log.warning("Couldn't seat user %s in league round %s", user_id, rnd.id)


def join(db: Session, user_id: int, at: datetime | None = None, played_at: datetime | None = None) -> None:
    """Seat a player in every track's current round. A run played earlier (an admin cleared a flagged run) only
    seats them in the tracks whose round it was played in is still running."""
    at = at or now()
    played_at = played_at or at
    for track in TRACKS:
        settle_due(db, track, at)
        if window(track, played_at)[0] != window(track, at)[0]:
            continue
        _seat(db, round_at(db, track, at), user_id)


@events.subscribe("run_recorded")
def _on_run(payload: dict) -> None:
    """Every unflagged run (and every cleared one) seats its player. Flagged runs never publish this."""
    if not flags.is_on(FLAG):
        return
    with SessionLocal() as db:
        run = db.get(Run, payload["run_id"])
        if run is None or run.flagged:
            return
        join(db, run.user_id, played_at=utc(run.created_at))


# ---- points


@dataclass
class Score:
    points: int = 0
    games: int = 0
    reached_at: datetime | None = None  # when their last counted best run came: ties go to whoever got there first


def scores(db: Session, rnd: LeagueRound) -> dict[int, Score]:
    """Every seated player's points this round, from their unflagged runs inside it."""
    players = select(LeagueEntry.user_id).where(LeagueEntry.round_id == rnd.id)
    runs = db.execute(
        select(Run.id, Run.user_id, Run.game_name, Run.score, Run.stats, Run.created_at)
        .where(Run.created_at >= rnd.starts_at, Run.created_at < rnd.ends_at, Run.flagged.is_(False),
               Run.user_id.in_(players))
    ).all()
    names = {r.game_name for r in runs}
    schemas = {g.name: g.leaderboard or boards.DEFAULT for g in db.scalars(select(Game).where(Game.name.in_(names)))}
    best: dict[str, dict[int, tuple]] = defaultdict(dict)  # game -> player -> (rank key, when, run id)
    for r in runs:
        schema = schemas.get(r.game_name, boards.DEFAULT)
        mine = (boards.rank_key(schema, boards.stats_of(schema, r.stats, r.score)), utc(r.created_at), r.id)
        if r.user_id not in best[r.game_name] or mine < best[r.game_name][r.user_id]:
            best[r.game_name][r.user_id] = mine
    per_player: dict[int, list[tuple[Fraction, datetime]]] = defaultdict(list)
    for players_best in best.values():
        keys = sorted(v[0] for v in players_best.values())
        n = len(keys)
        for user_id, (key, when, _) in players_best.items():
            if n == 1:
                pct = Fraction(100)
            else:
                beaten = n - bisect_right(keys, key)
                ties = bisect_right(keys, key) - bisect_left(keys, key) - 1
                pct = Fraction(100 * (2 * beaten + ties), 2 * (n - 1))
            per_player[user_id].append((pct, when))
    out = {}
    for user_id, games in per_player.items():
        counted = sorted(games, key=lambda g: (-g[0], g[1]))[:BEST_GAMES]
        total = sum((g[0] for g in counted), Fraction(0))
        out[user_id] = Score(points=math.floor(total + Fraction(1, 2)), games=len(games),
                             reached_at=max(g[1] for g in counted))
    return out


_NEVER = datetime.max.replace(tzinfo=timezone.utc)


def order_key(user_id: int, score: Score | None) -> tuple:
    """More points, then more games, then who got there first, then user id: always the same order."""
    score = score or Score()
    return (-score.points, -score.games, score.reached_at or _NEVER, user_id)


def active(score: Score | None) -> bool:
    return score is not None and score.games > 0


# ---- settling


def _tables(by_tier: dict[int, list[list[LeagueEntry]]]) -> list[tuple[list[LeagueEntry], bool]]:
    """The tables a round is settled in, each with whether anyone moves. Groups of MIN_TABLE or more are their own
    table; smaller ones are combined within the tier, then carried into the tier below (or its last full table)."""
    out: list[tuple[list[LeagueEntry], bool]] = []
    carry: list[LeagueEntry] = []
    for tier in reversed(range(len(TIERS))):
        groups = by_tier.get(tier, [])
        big = [list(g) for g in groups if len(g) >= MIN_TABLE]
        pool = carry + [e for g in groups if len(g) < MIN_TABLE for e in g]
        out += [(g, True) for g in big]
        if len(pool) >= MIN_TABLE:
            out.append((pool, True))
            carry = []
        elif pool and big:
            out[-1][0].extend(pool)
            carry = []
        else:
            carry = pool
    if carry:
        out.append((carry, False))  # waiting for players
    return out


def settle(db: Session, rnd: LeagueRound, at: datetime | None = None) -> None:
    """Places, points and moves for a round that has ended (once)."""
    at = at or now()
    if rnd.settled_at is not None or utc(rnd.ends_at) > at:
        return
    claimed = db.execute(update(LeagueRound).where(LeagueRound.id == rnd.id, LeagueRound.settled_at.is_(None))
                         .values(settled_at=at)).rowcount
    if claimed != 1:  # settled by another request at the same moment
        db.rollback()
        return
    sc = scores(db, rnd)
    entries = db.scalars(select(LeagueEntry).where(LeagueEntry.round_id == rnd.id)).all()
    groups = {g.id: g for g in db.scalars(select(LeagueGroup).where(LeagueGroup.round_id == rnd.id))}
    by_group: dict[int, list[LeagueEntry]] = defaultdict(list)
    for e in entries:
        if active(sc.get(e.user_id)):
            by_group[e.group_id].append(e)
        else:
            e.points, e.place, e.result = 0, None, "out"
    by_tier: dict[int, list[list[LeagueEntry]]] = defaultdict(list)
    for group_id in sorted(by_group, key=lambda gid: groups[gid].number):
        by_tier[groups[group_id].tier].append(by_group[group_id])
    members = {m.user_id: m for m in db.scalars(select(LeagueMember).where(
        LeagueMember.track == rnd.track, LeagueMember.user_id.in_([e.user_id for e in entries])))}
    promoted: list[tuple[int, int]] = []
    for table, moves in _tables(by_tier):
        ranked = sorted(table, key=lambda e: order_key(e.user_id, sc.get(e.user_id)))
        n = len(ranked)
        for place, e in enumerate(ranked, start=1):
            e.points, e.place = sc[e.user_id].points, place
            if moves and place <= MOVE and e.tier < len(TIERS) - 1:
                e.result = "up"
                members[e.user_id].tier = e.tier + 1
                promoted.append((e.user_id, e.tier + 1))
            elif moves and place > n - MOVE and e.tier > 0:
                e.result = "down"
                members[e.user_id].tier = e.tier - 1
            else:
                e.result = "stay"
    db.commit()
    if rnd.track in REWARD_TRACKS:
        events.publish("league_round_finished", {"track": rnd.track, "round_id": rnd.id})
        for user_id, tier in promoted:
            events.publish("league_promoted", {"track": rnd.track, "user_id": user_id, "tier": TIERS[tier]})


def settle_due(db: Session, track: str, at: datetime | None = None) -> None:
    at = at or now()
    for rnd in db.scalars(select(LeagueRound).where(LeagueRound.track == track, LeagueRound.ends_at <= at,
                                                    LeagueRound.settled_at.is_(None)).order_by(LeagueRound.starts_at)).all():
        settle(db, rnd, at)


# ---- reading


@dataclass
class Row:
    user: User
    place: int | None
    points: int
    games: int
    zone: str  # "up", "down", "stay" or "out" (no counted runs yet)


@dataclass
class Table:
    track: str
    round: LeagueRound
    tier: int  # the player's tier in this track
    group_number: int | None = None  # None: they haven't played this round yet
    waiting: bool = False  # under MIN_TABLE active players: nobody moves unless more join (or groups combine)
    rows: list[Row] | None = None
    me: Row | None = None
    points_to_promotion: int | None = None  # None: already in the promotion zone, top tier, or waiting


def standings(db: Session, track: str, user_id: int, at: datetime | None = None) -> Table:
    """The player's group in the track's current round, live."""
    at = at or now()
    settle_due(db, track, at)
    rnd = round_at(db, track, at)
    member = db.scalar(select(LeagueMember).where(LeagueMember.user_id == user_id, LeagueMember.track == track))
    out = Table(track=track, round=rnd, tier=member.tier if member else 0, rows=[])
    mine = db.scalar(select(LeagueEntry).where(LeagueEntry.round_id == rnd.id, LeagueEntry.user_id == user_id))
    if mine is None:
        return out
    group = db.get(LeagueGroup, mine.group_id)
    out.group_number, out.tier = group.number, mine.tier
    entries = db.scalars(select(LeagueEntry).where(LeagueEntry.group_id == group.id)).all()
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([e.user_id for e in entries])))}
    sc = scores(db, rnd)
    ranked = sorted(entries, key=lambda e: (not active(sc.get(e.user_id)), order_key(e.user_id, sc.get(e.user_id))))
    n = sum(active(sc.get(e.user_id)) for e in entries)
    out.waiting = n < MIN_TABLE
    for place, e in enumerate(ranked, start=1):
        s = sc.get(e.user_id) or Score()
        if not active(s):
            zone, shown = "out", None
        elif not out.waiting and place <= MOVE and e.tier < len(TIERS) - 1:
            zone, shown = "up", place
        elif not out.waiting and place > n - MOVE and e.tier > 0:
            zone, shown = "down", place
        else:
            zone, shown = "stay", place
        row = Row(user=users[e.user_id], place=shown, points=s.points, games=s.games, zone=zone)
        out.rows.append(row)
        if e.user_id == user_id:
            out.me = row
    if not out.waiting and out.me.zone != "up" and out.tier < len(TIERS) - 1:
        out.points_to_promotion = out.rows[MOVE - 1].points - out.me.points + 1
    return out


def history(db: Session, track: str, user_id: int, limit: int = HISTORY_LIMIT, at: datetime | None = None
            ) -> list[tuple[LeagueRound, LeagueEntry]]:
    """The player's settled rounds in a track, newest first."""
    settle_due(db, track, at)
    return list(db.execute(
        select(LeagueRound, LeagueEntry).join(LeagueEntry, LeagueEntry.round_id == LeagueRound.id)
        .where(LeagueRound.track == track, LeagueRound.settled_at.is_not(None), LeagueEntry.user_id == user_id)
        .order_by(LeagueRound.starts_at.desc()).limit(limit)
    ).tuples().all())
