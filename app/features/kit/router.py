from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Game, User
from ...security import optional_user
from . import service
from .schemas import SessionPingIn

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/kit", tags=["kit"])


@router.post("/games/{name}/sessions", status_code=status.HTTP_204_NO_CONTENT)
def ping(
    body: SessionPingIn,
    name: str = Path(max_length=40),
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
) -> Response:
    """A play-session ping from kit.js, passed on by start.ng's /connect page: start, heartbeat or end. Guests are
    counted too (without a player)."""
    if db.get(Game, name) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no game with that name")
    service.record(db, name, body.session_id, body.event, user.id if user else None, body.kit)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
