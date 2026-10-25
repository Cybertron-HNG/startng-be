"""Rooms (Referee mode): the live WebSocket and the online count. Both answer 404 while the "rooms" flag is off."""
from fastapi import APIRouter, Depends, WebSocket
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import sessionmaker

from ...live import read_ticket
from ...routers.live import _load, get_sessionmaker
from . import core
from .games import GAMES

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.get("/online")
def online(game: str):
    """How many signed-in people are in this game's rooms or waiting for a match, for the lobby's "N online"."""
    return {"game": game, "online": core.online(game)}


@router.get("/games")
def games():
    """The games start.ng referees, with how many players each takes."""
    return [{"game": g.name, "min": g.min_players, "max": g.max_players} for g in GAMES.values()]


@router.websocket("/live")
async def live(ws: WebSocket, game: str = "", ticket: str = "", room: str = "new", seats: int = 4, make_session: sessionmaker = Depends(get_sessionmaker)):
    """Play a refereed game. room is "new" (a room with a code to share), "match" (find someone), or a room's code. The
    ticket comes from POST /games/{game}/ticket, like the Hangout's. A game that start.ng does not referee gets a Relay room
    (seats: how many players it is for, 2 to 8): the server only passes the players' messages along."""
    user_id = read_ticket(ticket, game)
    loaded = await run_in_threadpool(_load, make_session, user_id, game) if user_id is not None else None
    await ws.accept()
    if loaded is None:
        await ws.close(core.BAD_TICKET, "Sign in with start.ng to play")
        return
    mode = room.strip()
    mode = mode.lower() if mode.lower() in ("new", "match") else mode.upper()
    await core.connect(ws, loaded, game, mode, seats)
