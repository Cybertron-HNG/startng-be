"""T2 creator hub: a shareable page for every creator (/by/<id>-<name>), a top-creators board (/by), and a private
dashboard in Your games with each game's plays, different players, repeat players and trend. Numbers are rolled up each
night (game_daily_stats) so history outlives the raw plays. Aggregates only: players are never identified."""
import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from ... import flags
from ...db import SessionLocal
from . import card, service  # noqa: F401  (card registers the "creator" share kind)
from .router import router  # noqa: F401

NAME = "creator_hub"
PERMISSIONS: dict[str, str] = {}
log = logging.getLogger(__name__)

ROLLUP_CHECK_SECONDS = 3600


async def run_forever() -> None:
    """The nightly rollup: every hour, write the finished days that aren't rolled up yet (a no-op most of the time)."""

    def tick() -> int:
        if not flags.is_on(NAME):
            return 0
        with SessionLocal() as db:
            return service.rollup_missing(db)

    while True:
        try:
            await run_in_threadpool(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("The creator hub rollup failed")
        await asyncio.sleep(ROLLUP_CHECK_SECONDS)


TASKS = [run_forever]
