"""The "tournament_result" card: how a player did in a finished daily tournament. The id is "<tournament id>.<user id>"
(the results are public on the leaderboard already, so the card shows nothing more)."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Tournament, TournamentPoints
from ...routers.games import _player_name
from ...routers.pages import _title
from . import service


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def describe(db: Session, item: str) -> service.Card | None:
    try:
        tournament_id, user_id = (int(part) for part in item.split("."))
    except ValueError:
        return None
    row = db.scalar(select(TournamentPoints).where(TournamentPoints.tournament_id == tournament_id, TournamentPoints.user_id == user_id))
    tournament = db.get(Tournament, tournament_id)
    if row is None or tournament is None:
        return None
    players = db.scalar(select(func.count()).select_from(TournamentPoints).where(TournamentPoints.tournament_id == tournament_id)) or 1
    name = _player_name(row.user)
    day = tournament.starts_at.strftime("%d %b %Y").lstrip("0")
    won = row.place == 1
    return service.Card(
        kicker="Daily tournament",
        title=f"{name} won the tournament!" if won else f"{name} came {ordinal(row.place)} of {players}",
        subtitle=f"{len(tournament.games)} games, {day}",
        numbers=[("Place", ordinal(row.place)), ("Points", str(row.points)), ("Players", str(players))],
        covers=[],
        target="/tournament",
        text=("I won today's start.ng tournament!" if won else f"I came {ordinal(row.place)} of {players} in today's start.ng tournament.")
             + " Think you can beat me?",
        description=f"{name} scored {row.points} points across {len(tournament.games)} games: "
                    + ", ".join(_title(g.game_name) for g in tournament.games[:5]),
    )


service.register("tournament_result", describe)
