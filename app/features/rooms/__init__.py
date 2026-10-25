"""Rooms for multiplayer games start.ng referees (tic-tac-toe, then chess ...): a code to share, matchmaking, a bot, and
coming back after a dropped connection. The flag is "rooms". See core.py for how it works and games/base.py for what a
game's rules module provides."""
import asyncio

from ... import flags as _flags
from . import core
from .router import router  # noqa: F401

NAME = "rooms"
PERMISSIONS: dict[str, str] = {}

UPKEEP_EVERY = 5  # seconds between sweeps for rooms that ran out of time
TICK_EVERY = 1  # seconds between asking hosted games (a quiz, bingo) whether a timer has run out


async def run_forever() -> None:
    """Hosted games' timers every second, and closing rooms that ran out of time every few, while the server is up and the flag is on."""
    beats = 0
    while True:
        await asyncio.sleep(TICK_EVERY)
        beats += 1
        if not _flags.is_on(NAME):
            continue
        try:
            await core.tick()
            if beats % UPKEEP_EVERY == 0:
                await core.sweep()
        except Exception:  # one bad room must not stop the upkeep
            pass


TASKS = [run_forever]
