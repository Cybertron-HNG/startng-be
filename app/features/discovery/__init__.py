"""A1 discovery: how games are ranked (game_rank, C10), the homepage shelves (Featured, Staff picks, New this week, Most
played, Most improved, Continue playing), featuring with a checklist owners can see, and "play next" suggestions."""
import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from ... import flags
from ...db import SessionLocal
from . import service  # noqa: F401  (registers the startng.json "mobile" section)
from .router import router  # noqa: F401

NAME = "discovery"
PERMISSIONS: dict[str, str] = {
    "featured.edit": "Choose the featured games and staff picks on the homepage",
}
log = logging.getLogger(__name__)

RANK_EVERY_SECONDS = 600


async def run_forever() -> None:
    """Rewrite game_rank every 10 minutes (reads also refresh it when it is more than 30 minutes old)."""

    def tick() -> int:
        if not flags.is_on(NAME):
            return 0
        with SessionLocal() as db:
            return service.recompute(db)

    while True:
        try:
            await run_in_threadpool(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ranking games failed")
        await asyncio.sleep(RANK_EVERY_SECONDS)


TASKS = [run_forever]
