"""Rooms for multiplayer games start.ng referees (tic-tac-toe, then chess ...): a code to share, matchmaking, a bot, and
coming back after a dropped connection. The flag is "rooms". See core.py for how it works and games/base.py for what a
game's rules module provides."""
import asyncio

from ... import flags as _flags
from . import core
from .router import router  # noqa: F401

NAME = "rooms"
PERMISSIONS: dict[str, str] = {}

UPKEEP_EVERY = 5  # seconds


async def run_forever() -> None:
    """Closes rooms that ran out of time and offers bots in the matchmaking line, while the server is up and the flag is on."""
    while True:
        await asyncio.sleep(UPKEEP_EVERY)
        if _flags.is_on(NAME):
            try:
                await core.sweep()
            except Exception:  # one bad room must not stop the upkeep
                pass


TASKS = [run_forever]
