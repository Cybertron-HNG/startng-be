import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ... import leaderboard as boards
from ... import tournaments
from ...db import get_db
from ...models import User
from ...routers.games import _game_or_404, _player_name, _ranked, _schema
from ...schemas import LeaderboardEntry, OverallEntry
from ...security import current_user
from ..crews import service as crews

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/boards", tags=["scoped boards"])

SCOPE = Query(pattern=r"^crew:\d{1,10}$", description='Who is on the board: "crew:<id>" (friends comes with the friends list)')


def _color(user: User) -> str | None:
    return ((user.character or {}).get("style") or {}).get("primaryColor")


def _players(db: Session, scope: str, user: User) -> list[int]:
    """The players a scope means. A crew's board is for its members only: to anyone else it doesn't exist."""
    crew_id = int(re.fullmatch(r"crew:(\d+)", scope).group(1))
    if not crews.is_member(db, crew_id, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no board here")
    return crews.members_of(db, crew_id)


@router.get("/overall", response_model=list[OverallEntry])
def overall(
    scope: str = SCOPE,
    period: Literal["all", "week"] = "all",
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The overall leaderboard (daily tournament points) for one crew."""
    rows = tournaments.overall(db, period, limit, only_users=_players(db, scope, user))
    return [OverallEntry(rank=i, player_id=r.user.id, name=_player_name(r.user), color=_color(r.user), points=r.points, wins=r.wins, firsts=r.firsts)
            for i, r in enumerate(rows, start=1)]


@router.get("/game/{name}", response_model=list[LeaderboardEntry])
def game(
    name: str,
    scope: str = SCOPE,
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """One game's board for one crew, ranked the way the game ranks (its own stats)."""
    members = set(_players(db, scope, user))
    g = _game_or_404(db, name)
    schema = _schema(g)
    rows = [s for s in _ranked(db, name, schema) if s.user_id in members][:limit]
    return [LeaderboardEntry(rank=i, player_id=s.user_id, name=_player_name(s.user), color=_color(s.user), score=s.best,
                             stats=boards.stats_of(schema, s.stats, s.best)) for i, s in enumerate(rows, start=1)]
