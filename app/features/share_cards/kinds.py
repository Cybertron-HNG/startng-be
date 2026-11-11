"""Kinds for start.ng's own code, which has no feature folder of its own to describe them in. Every other kind is
described by the feature that owns it (service.KINDS says who)."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ... import tournaments
from ...config import settings
from ...models import Game, Score, Tournament, TournamentPoints, User
from ...routers.games import _player_name, _ranked, _schema
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


def score(db: Session, id: str) -> service.Card | None:
    """A player's best score in a game.
    id: "<game_name>-<user_id>" or numeric "<score_id>".
    """
    row: Score | None = None
    if id.isdigit():
        row = db.get(Score, int(id))
    elif "-" in id:
        parts = id.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            row = db.scalar(select(Score).where(Score.game_name == parts[0], Score.user_id == int(parts[1])))
    if row is None:
        return None

    user = db.get(User, row.user_id)
    game = db.get(Game, row.game_name)
    if user is None or game is None:
        return None

    name = _player_name(user)
    game_title = _title(game.name)
    score_val = row.best
    schema = _schema(game)
    rows = _ranked(db, game.name, schema)
    rank = next((i + 1 for i, r in enumerate(rows) if r.user_id == user.id), None)

    if rank:
        place = _ordinal(rank)
        facts = [(f"{score_val:,}", "score"), (place, "rank")]
        badge = service.Badge(str(rank), place[len(str(rank)):], "rank", TONES.get(rank, "lime"))
    else:
        facts = [(f"{score_val:,}", "score")]
        badge = service.Badge(f"{score_val:,}", label="score", tone="lime")

    return service.Card(
        eyebrow=f"Score · {game_title}",
        name=name,
        title=f"scored {score_val:,}",
        facts=facts,
        game=game_title,
        badge=badge,
        cta=f"Can you beat {score_val:,}?",
        action=f"Play {game_title}",
        path=f"/games/{game.name}",
        text=f"I scored {score_val:,} on {game_title} on start.ng. Can you beat me?",
    )


service.register("score", score)
