"""Game health (H2): a background job looks at every live game about every 20 minutes. A game that fails
FAILS_IN_A_ROW checks in a row is unhealthy: admins see it, its owner sees it in Your games, and
game_unhealthy is published (Discovery drops it from featured). One good check makes it healthy again.
F8: an unhealthy game is also hidden through "needs fixing", and its owner and the super admins are emailed
(auto_hide.py), unless many games fail at once.

A check, without a browser:
  - index.html is there and isn't blank (some script, canvas or text to show);
  - every script and stylesheet index.html loads from the game's own folder is there;
  - the game's address answers 200 (GAME_HEALTH_HTTP; off on a laptop without the Worker).
"""
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import requests
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ... import events, flags
from ...config import settings
from ...db import SessionLocal
from ...game_store import GameStore, get_game_store
from ...models import Game
from ...storage import GAME_NAME_RE, game_url
from .models import GameHealth

log = logging.getLogger(__name__)

FLAG = "game_health"
NEVER = {"hangout"}  # start.ng's own space, not served from the games bucket
FAILS_IN_A_ROW = 3
CHECK_EVERY = timedelta(minutes=20)  # a healthy game
RECHECK_EVERY = timedelta(minutes=10)  # a failing one, so a broken game is unhealthy within about half an hour
PER_TICK = 20  # games checked per run of the background loop (every minute)
# Hidden games whose address stays online, so they're still checked (and their address opened): a new game waiting
# for review (F2) is unlisted, not taken offline. They're never hidden by a health check.
ONLINE_WHILE_HIDDEN = ("in_review",)


def _online(game: Game | None) -> bool:
    return game is None or game.hidden is None or game.hidden in ONLINE_WHILE_HIDDEN


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when  # SQLite drops the zone


@dataclass
class Problem:
    code: str  # no_page, blank_page, missing_file, not_loading
    message: str


# ---- the check


class _Assets(HTMLParser):
    """The scripts and stylesheets a page loads, and whether it has anything to show."""

    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []
        self.shows = False
        self.text = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            self.shows = True
            if a.get("src"):
                self.assets.append(a["src"])
        elif tag == "link" and "stylesheet" in (a.get("rel") or "").lower() and a.get("href"):
            self.assets.append(a["href"])
        elif tag in ("canvas", "iframe", "img", "svg", "video", "button"):
            self.shows = True

    def handle_data(self, data):
        self.text += len(data.strip())


def _local_path(src: str) -> str | None:
    """The file in the game's folder a src points to, or None for another site, data: and the like."""
    parts = urlsplit(src.strip())
    if parts.scheme or parts.netloc or not parts.path:
        return None
    return unquote(urljoin("/index.html", parts.path)).lstrip("/")


def _address(name: str) -> str:
    if not GAME_NAME_RE.match(name):  # only ever a game's own address, never a URL a caller made up
        raise ValueError("Not a game name")
    url = game_url(name)
    return url.replace("https://", "http://") if "localhost" in settings.games_base_domain else url


def fetch_status(url: str) -> int | str:
    """The status code the address answers with, or why it didn't answer."""
    try:
        return requests.get(url, timeout=15, allow_redirects=True, headers={"User-Agent": "start.ng health check"}).status_code
    except requests.RequestException as e:
        return type(e).__name__


def check(store: GameStore, name: str, fetch=fetch_status, http: bool = True) -> list[Problem]:
    """What's wrong with a game right now. An empty list: it's fine. http=False skips opening its address (a hidden
    game's address answers with an error page on purpose)."""
    page = store.read_file(name, "index.html")
    if page is None:
        return [Problem("no_page", "index.html isn't there, so the game can't open.")]
    parser = _Assets()
    try:
        parser.feed(page.decode("utf-8", "replace"))
    except Exception:
        pass
    problems: list[Problem] = []
    if not parser.shows and parser.text < 20:
        problems.append(Problem("blank_page", "index.html has no script, canvas or text, so the page is blank."))
    files = store.file_sizes(name)
    for src in dict.fromkeys(parser.assets):
        path = _local_path(src)
        if path is not None and path not in files:
            problems.append(Problem("missing_file", f'index.html loads "{src}", but there\'s no {path} in the game.'))
    if settings.game_health_http and http:
        status = fetch(_address(name))
        if status != 200:
            why = f"answered {status}" if isinstance(status, int) else f"didn't answer ({status})"
            problems.append(Problem("not_loading", f"The game's address {why}."))
    return problems[:10]


# ---- recording results


def health_of(db: Session, name: str) -> GameHealth:
    row = db.get(GameHealth, name)
    if row is None:
        row = GameHealth(game_name=name, status="unchecked", failures=0)
        db.add(row)
    return row


def record(db: Session, name: str, problems: list[Problem]) -> tuple[GameHealth, bool]:
    """Save a check. Returns the row and whether the game has just become unhealthy. The caller commits."""
    row = health_of(db, name)
    row.checked_at = now()
    if not problems:
        row.status, row.failures, row.problems, row.last_ok_at, row.unhealthy_since = "ok", 0, None, row.checked_at, None
        return row, False
    row.failures = (row.failures or 0) + 1
    row.problems = [asdict(p) for p in problems]
    if row.failures >= FAILS_IN_A_ROW:
        became = row.status != "unhealthy"
        row.status = "unhealthy"
        row.unhealthy_since = row.unhealthy_since or row.checked_at
        return row, became
    row.status = "failing"
    return row, False


def check_and_record(db: Session, store: GameStore, name: str, fetch=fetch_status, hide: bool = True) -> GameHealth:
    """Check one game now, save it, and tell the others if it just became unhealthy. hide: also hide it if it's
    unhealthy (F8, auto_hide.py); the background round does that itself, for all its games at once."""
    game = db.get(Game, name)
    row, became = record(db, name, check(store, name, fetch, http=_online(game)))
    db.commit()
    if became:
        events.publish("game_unhealthy", {"game": name, "reason": row.problems[0]["code"]})
    if hide:
        from . import auto_hide

        auto_hide.act(db, store, [name], checked=1, failed=int(row.status != "ok"))
    return row


def due(db: Session, at: datetime | None = None, limit: int = PER_TICK) -> list[str]:
    """Live games (and games waiting for review) whose next check is due: never checked first, then the longest ago."""
    at = at or now()
    q = (select(Game.name, GameHealth.status, GameHealth.checked_at)
         .outerjoin(GameHealth, GameHealth.game_name == Game.name)
         .where(or_(Game.hidden.is_(None), Game.hidden.in_(ONLINE_WHILE_HIDDEN)), Game.name.not_in(NEVER),
                or_(GameHealth.checked_at.is_(None), GameHealth.checked_at <= at - RECHECK_EVERY))
         .order_by(GameHealth.checked_at.is_not(None), GameHealth.checked_at))
    out = []
    for name, status, checked_at in db.execute(q).all():
        wait = CHECK_EVERY if status == "ok" else RECHECK_EVERY
        if checked_at is None or at - _utc(checked_at) >= wait:
            out.append(name)
        if len(out) >= limit:
            break
    return out


def check_due(db: Session, store: GameStore | None, fetch=fetch_status) -> int:
    if store is None:
        return 0
    from . import auto_hide

    names = due(db)
    failed = 0
    for name in names:
        try:
            row = check_and_record(db, store, name, fetch, hide=False)
            failed += row.status != "ok"
        except Exception:
            db.rollback()
            log.exception("Couldn't check %s", name)
    try:
        auto_hide.act(db, store, names, checked=len(names), failed=failed)
    except Exception:
        db.rollback()
        log.exception("Couldn't hide unhealthy games")
    return len(names)


# ---- a new version gets a fresh start (only while the feature is on)


def _on_publish(payload: dict) -> None:
    if not flags.is_on(FLAG):
        return
    with SessionLocal() as db:
        row = db.get(GameHealth, payload["game"])
        if row is not None:
            row.status, row.failures, row.problems, row.checked_at, row.unhealthy_since = "unchecked", 0, None, None, None
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
            log.exception("Game health checks failed")
        await asyncio.sleep(TICK_SECONDS)


