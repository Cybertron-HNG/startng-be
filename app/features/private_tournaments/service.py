"""Private tournaments: the rules, and what other features may call.

A player makes one (a title, 1 to 5 games that have leaderboards, a window of 1 day to 1 month from now) and shares its
invite link. Members' runs on those games inside the window count: each game gives points by place (the daily
tournament's 10/7/5/3/1) and the most points wins. Nothing here gives global points, XP or rewards. Standings are
worked out when someone looks, so nothing runs on a timer.

Runs arrive through the "score_submitted" event (every score a game sends). When the Progress Engine (D1) publishes
"run_recorded", this can read the run log instead.
"""
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import events, tournaments
from ... import leaderboard as boards
from ...activity import record as log_activity
from ...db import SessionLocal
from ...models import Game, User
from .models import PrivateTournament, PrivateTournamentGame, PrivateTournamentMember, PrivateTournamentScore

MAX_ACTIVE_OWNED = 5  # tournaments a player can have running at once
MAX_MEMBERS = 200
MIN_HOURS = 24
MAX_HOURS = 720  # 30 days


class Refused(Exception):
    """A rule was broken. `code` is for the website, `message` for the player."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def ended(t: PrivateTournament, at: datetime | None = None) -> bool:
    return tournaments.utc(t.ends_at) <= (at or tournaments.now())


def find(db: Session, code: str) -> PrivateTournament | None:
    return db.scalar(select(PrivateTournament).where(PrivateTournament.code == code))


def is_member(db: Session, t: PrivateTournament, user_id: int) -> bool:
    return db.scalar(select(PrivateTournamentMember.id).where(
        PrivateTournamentMember.tournament_id == t.id, PrivateTournamentMember.user_id == user_id)) is not None


def member_count(db: Session, t: PrivateTournament) -> int:
    return db.scalar(select(func.count()).select_from(PrivateTournamentMember).where(PrivateTournamentMember.tournament_id == t.id)) or 0


def create(db: Session, owner: User, title: str, games: list[str], hours: int, at: datetime | None = None) -> PrivateTournament:
    at = at or tournaments.now()
    names = list(dict.fromkeys(games))  # the same game twice is once
    if not 1 <= len(names) <= 5:
        raise Refused("games", "Pick between 1 and 5 games.")
    if not MIN_HOURS <= hours <= MAX_HOURS:
        raise Refused("duration", "A tournament runs from 1 day to 1 month.")
    allowed = set(tournaments.eligible_games(db))
    bad = [n for n in names if n not in allowed]
    if bad:
        raise Refused("game_not_allowed", f"{bad[0]} can't be used: it needs a leaderboard and to be listed on start.ng.")
    active = db.scalar(select(func.count()).select_from(PrivateTournament).where(
        PrivateTournament.owner_id == owner.id, PrivateTournament.ends_at > at)) or 0
    if active >= MAX_ACTIVE_OWNED:
        raise Refused("too_many", f"You already have {MAX_ACTIVE_OWNED} tournaments running. Wait for one to end.")
    t = PrivateTournament(
        code=secrets.token_urlsafe(8), owner_id=owner.id, title=title.strip(), starts_at=at, ends_at=at + timedelta(hours=hours),
        games=[PrivateTournamentGame(game_name=n, position=i) for i, n in enumerate(names)],
        members=[PrivateTournamentMember(user_id=owner.id)],
    )
    db.add(t)
    log_activity(db, owner, "player", "private_tournament.created", t.code, games=names, hours=hours)
    db.commit()
    return t


def join(db: Session, t: PrivateTournament, user: User, at: datetime | None = None) -> None:
    """Join by the invite link. Joining twice is fine. Runs before joining don't count."""
    if is_member(db, t, user.id):
        return
    if ended(t, at):
        raise Refused("ended", "This tournament has ended.")
    if member_count(db, t) >= MAX_MEMBERS:
        raise Refused("full", "This tournament is full.")
    db.add(PrivateTournamentMember(tournament_id=t.id, user_id=user.id))
    try:
        db.commit()
    except IntegrityError:  # joined twice at once
        db.rollback()


def leave(db: Session, t: PrivateTournament, user: User) -> None:
    """A member leaves (and their runs go with them). The one who made it deletes it instead."""
    if t.owner_id == user.id:
        raise Refused("owner", "You made this tournament. Delete it instead.")
    db.execute(delete(PrivateTournamentScore).where(
        PrivateTournamentScore.tournament_id == t.id, PrivateTournamentScore.user_id == user.id))
    db.execute(delete(PrivateTournamentMember).where(
        PrivateTournamentMember.tournament_id == t.id, PrivateTournamentMember.user_id == user.id))
    db.commit()


def new_code(db: Session, t: PrivateTournament, owner: User) -> None:
    """A new invite link. Whoever only has the old one can't join; members stay."""
    t.code = secrets.token_urlsafe(8)
    log_activity(db, owner, "player", "private_tournament.link_changed", t.code)
    db.commit()


def mine(db: Session, user: User) -> list[PrivateTournament]:
    """Tournaments the player is in: running ones first, then the most recent that ended."""
    rows = db.scalars(select(PrivateTournament).join(PrivateTournamentMember).where(
        PrivateTournamentMember.user_id == user.id)).all()
    now = tournaments.now()
    running = sorted((t for t in rows if not ended(t, now)), key=lambda t: tournaments.utc(t.ends_at))
    done = sorted((t for t in rows if ended(t, now)), key=lambda t: tournaments.utc(t.ends_at), reverse=True)
    return running + done


# ---- runs and results


def record_run(db: Session, user_id: int, game: Game, stats: dict, schema: list[dict], at: datetime | None = None) -> None:
    """Count a run in every tournament the player is a member of, that uses this game and is running. Keeps their best."""
    at = at or tournaments.now()
    first = stats[schema[0]["key"]]
    for t in db.scalars(select(PrivateTournament).join(PrivateTournamentGame).join(
        PrivateTournamentMember, PrivateTournamentMember.tournament_id == PrivateTournament.id,
    ).where(
        PrivateTournamentGame.game_name == game.name, PrivateTournamentMember.user_id == user_id,
        PrivateTournament.starts_at <= at, PrivateTournament.ends_at > at,
    )).all():
        where = (PrivateTournamentScore.tournament_id == t.id, PrivateTournamentScore.game_name == game.name,
                 PrivateTournamentScore.user_id == user_id)
        row = db.scalar(select(PrivateTournamentScore).where(*where))
        if row is None:
            db.add(PrivateTournamentScore(tournament_id=t.id, game_name=game.name, user_id=user_id, best=first, stats=stats))
            try:
                db.commit()
                continue
            except IntegrityError:  # two first runs at once: compare with the one that won
                db.rollback()
                row = db.scalar(select(PrivateTournamentScore).where(*where))
        if boards.is_better(schema, stats, boards.stats_of(schema, row.stats, row.best)):
            row.best, row.stats = first, stats
            db.commit()


@events.subscribe("score_submitted")
def on_score_submitted(payload: dict) -> None:
    with SessionLocal() as db:
        game = db.get(Game, payload["game"])
        if game is None:
            return
        record_run(db, payload["user_id"], game, payload["stats"], game.leaderboard or boards.DEFAULT)


@dataclass
class Standing:
    user: User
    points: int = 0
    firsts: int = 0
    last_at: datetime | None = None
    place: int = 0


@dataclass
class Results:
    standings: list[Standing]
    boards: dict[str, list[PrivateTournamentScore]]  # each game's runs, best first


def results(db: Session, t: PrivateTournament) -> Results:
    """Each game's board and the standings (so far, or final once it has ended). Only current members count."""
    members = {m.user_id for m in t.members}
    per_game: dict[str, list[PrivateTournamentScore]] = defaultdict(list)
    for s in db.scalars(select(PrivateTournamentScore).where(PrivateTournamentScore.tournament_id == t.id)).all():
        if s.user_id in members:
            per_game[s.game_name].append(s)
    by_user: dict[int, Standing] = {}
    out_boards: dict[str, list[PrivateTournamentScore]] = {}
    for tg in t.games:
        schema = tg.game.leaderboard or boards.DEFAULT
        ranked = sorted(per_game.get(tg.game_name, []),
                        key=lambda s: (boards.rank_key(schema, boards.stats_of(schema, s.stats, s.best)), tournaments.utc(s.updated_at)))
        out_boards[tg.game_name] = ranked
        for place, s in enumerate(ranked, start=1):
            st = by_user.setdefault(s.user_id, Standing(user=s.user))
            st.points += tournaments.points_for(place)
            st.firsts += place == 1
            when = tournaments.utc(s.updated_at)
            st.last_at = when if st.last_at is None or when > st.last_at else st.last_at
    standings = sorted(by_user.values(), key=lambda st: (-st.points, -st.firsts, st.last_at, st.user.id))
    for place, st in enumerate(standings, start=1):
        st.place = place
    return Results(standings=standings, boards=out_boards)
