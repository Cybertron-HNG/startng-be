"""Kinds for start.ng's own code, which has no feature folder of its own to describe them in. Every other kind is
described by the feature that owns it (service.KINDS says who)."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ... import tournaments
from ...config import settings
from ...models import Tournament, TournamentPoints
from ...routers.games import _player_name
from ...routers.pages import _title
from . import service

TONES = {1: "gold", 2: "silver", 3: "bronze"}  # the badge ring for the top three


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def tournament_result(db: Session, id: str) -> service.Card | None:
    """A player's place in a finished daily tournament. id: "<tournament id>-<player id>". Only what the overall
    leaderboard already shows (name, place, points), so anyone with the link may see it. When player profiles (S1) can
    be made private, a private player's card should answer None here, like their profile page."""
    try:
        tournament_id, user_id = (int(part) for part in id.split("-"))
    except ValueError:
        return None
    row = db.scalar(select(TournamentPoints).options(selectinload(TournamentPoints.user))
                    .where(TournamentPoints.tournament_id == tournament_id, TournamentPoints.user_id == user_id))
    t = db.scalar(select(Tournament).options(selectinload(Tournament.games)).where(Tournament.id == tournament_id))
    if row is None or t is None or t.finished_at is None:
        return None
    place = _ordinal(row.place)
    points = f"{row.points} {'point' if row.points == 1 else 'points'}"
    games = [_title(g.game_name) for g in t.games]
    day = tournaments.utc(t.ends_at) + timedelta(hours=settings.tournament_utc_offset)  # the day it ended, Nigeria time
    return service.Card(
        eyebrow=f"Daily tournament · {day:%a} {day.day} {day:%b}",
        name=_player_name(row.user),
        title=f"placed {place}",
        facts=[(str(row.points), "point" if row.points == 1 else "points"),
               (str(len(games)), "game" if len(games) == 1 else "games")],
        game=games[0] if len(games) == 1 else f"{games[0]} +{len(games) - 1}" if games else "",
        badge=service.Badge(str(row.place), place[-2:], "place", TONES.get(row.place, "lime")),
        cta=f"Think you can beat {points}?",
        path="/tournament",
        text=f"I placed {place} in the start.ng daily tournament with {points}. A new one starts every day:",
    )


service.register("tournament_result", tournament_result)
