from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from .. import flags
from ..activity import mark_seen
from ..db import SessionLocal, get_db
from ..live import (
    BAD_TICKET, MAX_PLAYERS, ROOM_FULL, TICKET_SECONDS, Player, Resident,
    create_ticket, read_ticket, room_for, run_player,
)
from ..models import Game, User
from ..security import current_user

router = APIRouter(prefix="/games", tags=["live"])


class TicketOut(BaseModel):
    ticket: str
    expires_in: int  # seconds


def get_sessionmaker() -> sessionmaker:
    """A live connection can stay open for hours, so it opens its own short session instead of holding one."""
    return SessionLocal


@router.post("/{name}/ticket", response_model=TicketOut)
def ticket(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A one-minute ticket into a game's live room. Games get it through start.ng's /connect page."""
    if not db.get(Game, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No game with that name")
    mark_seen(db, user, force=True)  # coming into a room counts as being active right now
    return TicketOut(ticket=create_ticket(user.id, name), expires_in=TICKET_SECONDS)


def _resident(user: User, db: Session | None = None) -> Resident:
    character = user.character or {}
    style = character.get("style")
    if db is not None and style and flags.is_on("wardrobe"):  # what they wear, so everyone in the room sees it
        from ..features.wardrobe import service as wardrobe
        worn = wardrobe.style_overrides(db, user.id)
        if worn["items"]:
            style = {**style, **worn}
    return Resident(user.id, character.get("name") or user.name or "Player", style)


def _load(make_session: sessionmaker, user_id: int, game: str) -> Resident | None:
    """The joining player's name and look, if they and the game exist."""
    with make_session() as db:
        user = db.get(User, user_id)
        if user is None or db.get(Game, game) is None:
            return None
        return _resident(user, db)


@router.websocket("/{name}/live")
async def live(ws: WebSocket, name: str, ticket: str = "", make_session: sessionmaker = Depends(get_sessionmaker)):
    """A game's live room: everyone connected sees each other move, wave and chat. Nobody else is shown."""
    user_id = read_ticket(ticket, name)
    loaded = await run_in_threadpool(_load, make_session, user_id, name) if user_id is not None else None
    await ws.accept()
    if loaded is None:
        await ws.close(BAD_TICKET, "Sign in with start.ng to join")
        return
    room = room_for(name)
    if len(room.players) >= MAX_PLAYERS and user_id not in room.players:
        await ws.close(ROOM_FULL, "The room is full, try again soon")
        return
    await run_player(room, Player(id=loaded.id, name=loaded.name, style=loaded.style, ws=ws))
