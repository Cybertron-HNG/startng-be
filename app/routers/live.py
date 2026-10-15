from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from ..db import SessionLocal, get_db
from ..live import BAD_TICKET, MAX_PLAYERS, ROOM_FULL, TICKET_SECONDS, Player, create_ticket, read_ticket, room_for, run_player
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
    return TicketOut(ticket=create_ticket(user.id, name), expires_in=TICKET_SECONDS)


def _load_player(make_session: sessionmaker, user_id: int, game: str) -> tuple[str, dict | None] | None:
    with make_session() as db:
        user = db.get(User, user_id)
        if user is None or db.get(Game, game) is None:
            return None
        character = user.character or {}
        return character.get("name") or user.name or "Player", character.get("style")


@router.websocket("/{name}/live")
async def live(ws: WebSocket, name: str, ticket: str = "", make_session: sessionmaker = Depends(get_sessionmaker)):
    """A game's live room: everyone in it sees each other move and wave."""
    user_id = read_ticket(ticket, name)
    profile = await run_in_threadpool(_load_player, make_session, user_id, name) if user_id is not None else None
    await ws.accept()
    if profile is None:
        await ws.close(BAD_TICKET, "Sign in with start.ng to join")
        return
    room = room_for(name)
    if len(room.players) >= MAX_PLAYERS and user_id not in room.players:
        await ws.close(ROOM_FULL, "The room is full, try again soon")
        return
    player_name, style = profile
    await run_player(room, Player(id=user_id, name=player_name, style=style, ws=ws))
