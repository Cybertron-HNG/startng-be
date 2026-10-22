from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...routers.games import _player_name
from ...security import current_user
from . import service
from .schemas import HistoryOut, HistoryRowOut, LeaguesOut, PlayerOut, RowOut, TableOut, TrackOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
# There is no join route: a player's first unflagged run in a round seats them (service.join).
router = APIRouter(prefix="/leagues", tags=["leagues"])
TOP = 3


def _track_or_404(track: str) -> str:
    if track not in service.TRACKS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No league track with that name")
    return track


def _row(row: service.Row, user_id: int) -> RowOut:
    u = row.user
    return RowOut(place=row.place, player=PlayerOut(id=u.id, name=_player_name(u), character=u.character), points=row.points,
                  games=row.games, zone=row.zone, me=u.id == user_id)


def _table(t: service.Table, user_id: int) -> TableOut:
    return TableOut(
        track=t.track, starts_at=service.utc(t.round.starts_at), ends_at=service.utc(t.round.ends_at),
        tier=service.TIERS[t.tier], group=t.group_number, waiting=t.waiting, points_to_promotion=t.points_to_promotion,
        me=_row(t.me, user_id) if t.me else None, rows=[_row(r, user_id) for r in t.rows or []],
    )


@router.get("", response_model=LeaguesOut)
def overview(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Every track: its countdown, the player's tier and group, and the top of their group."""
    out = []
    for track in service.TRACKS:
        t = _table(service.standings(db, track, user.id), user.id)
        out.append(TrackOut(track=track, starts_at=t.starts_at, ends_at=t.ends_at, tier=t.tier, group=t.group,
                            me=t.me, top=t.rows[:TOP]))
    return LeaguesOut(tracks=out)


@router.get("/{track}", response_model=TableOut)
def table(track: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The player's group in this track's current round, with the promotion and relegation zones."""
    return _table(service.standings(db, _track_or_404(track), user.id), user.id)


@router.get("/{track}/history", response_model=HistoryOut)
def history(track: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The player's finished rounds in this track, newest first."""
    rounds = service.history(db, _track_or_404(track), user.id)
    return HistoryOut(track=track, rounds=[
        HistoryRowOut(starts_at=service.utc(r.starts_at), ends_at=service.utc(r.ends_at), tier=service.TIERS[e.tier],
                      place=e.place, points=e.points or 0, result=e.result or "stay")
        for r, e in rounds
    ])
