from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ...activity import record
from ...db import get_db
from ...models import User
from ...routers.games import _game_or_404
from ...security import current_user
from ..moderation.service import require_clean
from . import service
from .schemas import PulseOut, VoteIn, VoteOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["player pulse"])


@router.post("/games/{name}/pulse", response_model=VoteOut, status_code=status.HTTP_201_CREATED)
def vote(name: str, body: VoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A signed-in player's answer to "How was that?" after a round."""
    _game_or_404(db, name)
    require_clean(db, body.note, "Your note")
    row = service.vote(db, user.id, name, body.feel, body.note)
    record(db, user, "player", "player_pulse.voted")
    db.commit()
    return VoteOut(feel=row.feel, note=row.note)


@router.get("/games/{name}/pulse", response_model=PulseOut)
def pulse(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The game's creator only: what players said over the last 7 days."""
    game = _game_or_404(db, name)
    if game.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the game's creator can see what players said")
    return service.summary(db, name)
