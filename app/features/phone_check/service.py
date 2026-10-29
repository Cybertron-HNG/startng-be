"""What other features may call (this feature's contract): queue(), and the background checker."""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import events, flags
from ...config import settings
from ...db import SessionLocal
from ...game_store import GameStore, get_game_store
from ...storage import game_url
from . import browser, files
from .models import PhoneCheck

log = logging.getLogger(__name__)

FLAG = "phone_check"
NEVER = {"hangout"}  # start.ng's own space, not a published game
PER_TICK = 5  # games checked per run of the background loop (every minute); a browser check takes a few seconds


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when  # SQLite drops the zone


def queue(db: Session, name: str) -> PhoneCheck:
    """Check this game soon (it was just published or updated, or its owner asked). The caller commits."""
    row = db.get(PhoneCheck, name)
    if row is None:
        row = PhoneCheck(game_name=name)
        db.add(row)
    row.status, row.queued_at = "unchecked", now()
    return row


def _address(name: str) -> str:
    url = game_url(name)
    return url.replace("https://", "http://") if "localhost" in settings.games_base_domain else url


def check(store: GameStore, name: str, open_in_browser=None) -> tuple[str, str, list[dict]]:
    """(status, method, findings) for a game now. open_in_browser(url) -> findings; None: browser.check when it's
    switched on and installed."""
    found = files.check(lambda path: store.read_file(name, path))
    if found is None:
        return "error", "files", [{"code": "no_page", "message": "index.html isn't there, so there's nothing to check."}]
    method = "files"
    if open_in_browser is None and settings.phone_check_browser and browser.available():
        open_in_browser = browser.check
    if open_in_browser is not None:
        try:
            seen = open_in_browser(_address(name))
        except Exception as e:  # the game didn't load in time, or the browser couldn't start: the file checks stand
            log.warning("Phone check couldn't open %s in a browser: %s", name, e)
        else:
            method = "browser"
            # The browser measured the real page, so it decides whether widths are a problem; the viewport findings stay.
            found = [f for f in found if f["code"] not in ("fixed_width", "wide_canvas")]
            if any(f["code"] in ("no_viewport", "viewport_width") for f in found):
                seen = [f for f in seen if f["code"] != "scrolls_sideways"]  # the viewport tag is why: one finding is enough
            found += seen
    return ("worth_a_look" if found else "ok"), method, found[:6]


def check_and_record(db: Session, store: GameStore, name: str, open_in_browser=None) -> PhoneCheck:
    row = db.get(PhoneCheck, name) or queue(db, name)
    row.status, row.method, row.findings = check(store, name, open_in_browser)
    row.checked_at, row.queued_at = now(), None
    db.commit()
    return row


def due(db: Session, limit: int = PER_TICK) -> list[str]:
    return list(db.scalars(select(PhoneCheck.game_name).where(PhoneCheck.status == "unchecked")
                           .order_by(PhoneCheck.queued_at).limit(limit)))


def check_due(db: Session, store: GameStore | None, open_in_browser=None) -> int:
    if store is None:
        return 0
    names = due(db)
    for name in names:
        try:
            check_and_record(db, store, name, open_in_browser)
        except Exception:
            db.rollback()
            log.exception("Phone check of %s failed", name)
    return len(names)


def _on_publish(payload: dict) -> None:
    if not flags.is_on(FLAG) or payload.get("game") in NEVER:
        return
    with SessionLocal() as db:
        queue(db, payload["game"])
        db.commit()


events.subscribe("game_published")(_on_publish)
events.subscribe("game_updated")(_on_publish)


TICK_SECONDS = 60


async def run_forever() -> None:
    """The background checker, started with the server; does nothing while the flag is off."""
    import asyncio

    from starlette.concurrency import run_in_threadpool

    def tick() -> int:
        if not flags.is_on(FLAG):
            return 0
        with SessionLocal() as db:
            return check_due(db, get_game_store())

    while True:
        try:
            await run_in_threadpool(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Phone checks failed")
        await asyncio.sleep(TICK_SECONDS)
