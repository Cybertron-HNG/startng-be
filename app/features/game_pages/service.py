"""Making, reviewing and reading the pages about games. The server pages and the admin both go through here."""
import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import auto_update, flags
from ...config import settings
from ...publish import PublishError, download_repo, parse_repo_url, read_brief
from ...db import SessionLocal
from ...game_store import get_game_store
from ...models import Game
from . import generate as gen
from .facts import read_facts
from .models import GamePage

log = logging.getLogger(__name__)
SIMILAR = 0.85  # two pages this alike are the same page: only the first is made


# What must never go public without a person: links, markup, addresses, and words that do not belong on a family-friendly page.
_UNSAFE = re.compile(r"https?:|www\.|<[a-z/!]|@\w|\bfuck|\bshit\b|\bbitch|\bcunt|\bnigg|\bporn|\bsex(?:y|ual)?\b|\bnude|\bdick\b|\bpussy|\bwhore|\bslut|\brape\b", re.I)


def is_safe(title: str, description: str, body: str) -> bool:
    """The check that stands in for a person reading the page: no links, markup or addresses, no bad words, and a sensible size.
    Pages are written only from the game's own words, so this is a net, not the main defence."""
    text = f"{title}\n{description}\n{body}"
    return bool(title.strip() and body.strip()) and len(title) <= 160 and len(description) <= 300 and len(body) <= 20000 and not _UNSAFE.search(text)


def _auto_approve(row: "GamePage") -> bool:
    """Approve a freshly made draft by itself, if switched on, no person has handled the page, and it passes is_safe."""
    if not settings.game_pages_auto_approve or row.status != "draft" or row.held or row.edited or not is_safe(row.title, row.description, row.body):
        return False
    row.status, row.approved_at, row.approved_by_id = "approved", datetime.now(timezone.utc), None
    return True


class PageError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _visible():
    return select(Game).where(Game.hidden.is_(None))


def _tags(db: Session, game_name: str) -> list[str]:
    try:
        from ..tags import service as tags
        return [t.label for t in tags.tags_of(db, game_name) if not t.everywhere]
    except Exception:  # the tags feature is optional here
        return []


def _similar_to(db: Session, page: GamePage, facts_title: str) -> str | None:
    mine = gen.shingles(page.body, facts_title)
    rows = db.execute(select(GamePage.game_name, GamePage.body).where(GamePage.kind == page.kind, GamePage.game_name != page.game_name, GamePage.status.in_(["draft", "approved"]))).all()
    for other_game, body in rows:
        other_title = " ".join(w.capitalize() for w in other_game.split("-"))
        if gen.similarity(mine, gen.shingles(body, other_title)) >= SIMILAR:
            return other_game
    return None


def generate_for(db: Session, store, game: Game, kinds: list[str] | None = None, force: bool = False) -> dict[str, str]:
    """Write (or rewrite) the draft pages of one game. Approved pages and pages a person edited are left alone unless
    force. Returns kind -> the status it ended up with."""
    facts = read_facts(store, game, _tags(db, game.name))
    done: dict[str, str] = {}
    for kind in kinds or gen.KINDS:
        row = db.scalar(select(GamePage).where(GamePage.game_name == game.name, GamePage.kind == kind))
        # A page a person approved, edited or took down is theirs. A page that approved itself is remade with the game's new facts.
        if row is not None and (row.edited or row.held or (row.status == "approved" and row.approved_by_id is not None)) and not force:
            done[kind] = row.status
            continue
        if row is None:
            row = GamePage(game_name=game.name, kind=kind)
            db.add(row)
        made = gen.build(kind, facts, game.name)
        row.facts, row.generated_at, row.edited, row.approved_at, row.approved_by_id = facts.as_dict(), datetime.now(timezone.utc), False, None, None
        if isinstance(made, str):
            row.status, row.reason, row.title, row.description, row.body = "skipped", made, "", "", ""
        else:
            row.title, row.description, row.body, row.reason = made.title, made.description, made.body, None
            row.status = "draft"
            db.flush()
            twin = _similar_to(db, row, facts.title)
            if twin:
                row.status, row.reason = "skipped", f"Too much like the {gen.SHORT[kind]} page of {twin}. Pages that say the same thing do not help anyone."
            else:
                _auto_approve(row)
        done[kind] = row.status
    db.commit()
    return done


def generate_all(db: Session, store, force: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {"draft": 0, "approved": 0, "skipped": 0}
    for game in db.scalars(_visible().order_by(Game.name)).all():
        for status in generate_for(db, store, game, force=force).values():
            counts[status] = counts.get(status, 0) + 1
    return counts


def get_page(db: Session, page_id: int) -> GamePage:
    page = db.get(GamePage, page_id)
    if page is None:
        raise PageError("No such page", 404)
    return page


def set_text(db: Session, page_id: int, title: str, description: str, body: str) -> GamePage:
    page = get_page(db, page_id)
    if page.status == "skipped":
        raise PageError("This page was not made, there is nothing to edit. Run Generate after the game's files change.", 409)
    title, description, body = title.strip(), description.strip(), body.strip()
    if not title or len(title) > 160 or len(description) > 300 or not body or len(body) > 20000:
        raise PageError("A page needs a title (up to 160 characters), a description (up to 300) and some text")
    page.title, page.description, page.body, page.edited = title, description, body, True
    if page.status == "approved":
        page.status, page.approved_at, page.approved_by_id = "draft", None, None  # changed text needs a fresh look
        page.held = True
    db.commit()
    return page


def approve(db: Session, page_id: int, admin_user_id: int) -> GamePage:
    page = get_page(db, page_id)
    if page.status == "skipped":
        raise PageError("This page was not made, so it can't be approved.", 409)
    page.status, page.approved_at, page.approved_by_id, page.held = "approved", datetime.now(timezone.utc), admin_user_id, False
    db.commit()
    return page


def unapprove(db: Session, page_id: int) -> GamePage:
    page = get_page(db, page_id)
    if page.status == "approved":
        page.status, page.approved_at, page.approved_by_id = "draft", None, None
        page.held = True  # a person took it down: it does not go public again unless a person approves it
        db.commit()
    return page


def approved_kinds(db: Session, game_name: str) -> list[str]:
    have = set(db.scalars(select(GamePage.kind).where(GamePage.game_name == game_name, GamePage.status == "approved")).all())
    return [k for k in gen.KINDS if k in have]


def approved_page(db: Session, game_name: str, kind: str) -> GamePage | None:
    return db.scalar(select(GamePage).where(GamePage.game_name == game_name, GamePage.kind == kind, GamePage.status == "approved"))


def queue(db: Session, status: str, limit: int, offset: int) -> list[GamePage]:
    q = select(GamePage).order_by(GamePage.game_name, GamePage.kind)
    if status != "all":
        q = q.where(GamePage.status == status)
    return list(db.scalars(q.limit(limit).offset(offset)).all())


def gaps(db: Session) -> list[dict]:
    """Visible games with no page at all (everything skipped, or never run) and the reasons: the list to send creators."""
    pages: dict[str, list[GamePage]] = {}
    for p in db.scalars(select(GamePage)).all():
        pages.setdefault(p.game_name, []).append(p)
    out = []
    for game in db.scalars(_visible().order_by(Game.name)).all():
        mine = pages.get(game.name, [])
        if any(p.status in ("draft", "approved") for p in mine):
            continue
        out.append({"game": game.name, "reasons": sorted({p.reason for p in mine if p.reason}) or ["Pages have not been made yet."]})
    return out


def counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(GamePage.status, func.count()).group_by(GamePage.status)).all()
    return {s: n for s, n in rows}


def sitemap_urls(db: Session) -> list[tuple[str, str | None]]:
    """Every approved page of a visible game, with the day it was approved."""
    from ... import flags
    if not flags.is_on("game_pages"):
        return []  # the routes answer 404 while the flag is off, so the sitemap must not list them
    rows = db.execute(
        select(GamePage.game_name, GamePage.kind, GamePage.approved_at).join(Game, Game.name == GamePage.game_name)
        .where(GamePage.status == "approved", Game.hidden.is_(None)).order_by(GamePage.game_name, GamePage.kind)
    ).all()
    return [(f"/games/{name}/{kind}", at.date().isoformat() if at else None) for name, kind, at in rows]


# ---- pages are made and approved automatically; an admin can edit or take any page down

AUTO_PER_ROUND = 10  # games per round: a new site with 180 games is done in a few minutes, without a spike
ROUND_SECONDS = 120


def make_missing_drafts(db: Session, store, limit: int = AUTO_PER_ROUND) -> int:
    """Write draft pages for games that have never had any (a new game, or the first run after switching the feature on).
    Games that already have rows are left alone here: their drafts refresh when they get a new version (on_game_updated).
    Returns how many games were done."""
    if store is None:
        return 0
    have = set(db.scalars(select(GamePage.game_name).distinct()).all())
    todo = [g for g in db.scalars(_visible().order_by(Game.created_at.desc())).all() if g.name not in have][:limit]
    for game in todo:
        generate_for(db, store, game)
    return len(todo)


BACKFILL_PER_ROUND = 5  # repos downloaded per round: GitHub is asked gently
HICCUPS = ("download_failed",)  # GitHub slow or down: try again later. Any other reason (repo gone, private, too big, a link that can't be read) is remembered


def backfill_briefs(db: Session, limit: int = BACKFILL_PER_ROUND) -> list[str]:
    """Read the brief (app/brief.py) of games that were published before it existed, from their GitHub repos, a few at a time.
    Returns the games that got one. A repo that can't be read (gone, private, too big, a link that doesn't parse) is marked so it isn't asked for again;
    a hiccup (GitHub slow, or its limit used up) leaves the game for a later round."""
    if auto_update.paused_until():
        return []  # GitHub's limit is used up: wait, like the update checker does
    done: list[str] = []
    for game in db.scalars(_visible().where(Game.brief.is_(None)).order_by(Game.created_at.desc()).limit(limit)).all():
        try:
            archive = download_repo(parse_repo_url(game.repo_url, game.branch))
        except PublishError as err:
            if err.code not in HICCUPS:
                game.brief = {"v": 1, "unavailable": err.code, "messages": [], "notes": [], "names": [], "readme": None, "files": 0}
                db.commit()
                continue
            log.warning("Couldn't read the repo of %s (%s); will try again later", game.name, err.code)
            break  # GitHub is not answering: stop for this round
        brief = read_brief(archive)
        if brief is not None:
            game.brief = brief
            db.commit()
            done.append(game.name)
    return done


def approve_waiting(db: Session, limit: int = 200) -> int:
    """Drafts already waiting (written before pages approved themselves): approve the ones that pass the safety check."""
    if not settings.game_pages_auto_approve:
        return 0
    n = 0
    for row in db.scalars(select(GamePage).where(GamePage.status == "draft", GamePage.held.is_(False), GamePage.edited.is_(False)).limit(limit)).all():
        n += _auto_approve(row)
    db.commit()
    return n


def on_game_updated(payload: dict) -> None:
    """A game got a new version: remake its draft pages (approved and edited pages are never touched), so a game that was
    too thin before gets pages once its creator adds words. Subscribed in __init__.py; never raises."""
    if not flags.is_on("game_pages"):
        return
    store = get_game_store()
    if store is None:
        return
    with SessionLocal() as db:
        game = db.get(Game, payload["game"])
        if game is not None and game.hidden is None:
            generate_for(db, store, game)


async def run_forever() -> None:
    """The background job, started with the server; does nothing while the flag is off."""
    from starlette.concurrency import run_in_threadpool

    def tick() -> int:
        if not flags.is_on("game_pages"):
            return 0
        with SessionLocal() as db:
            store = get_game_store()
            for name in backfill_briefs(db):  # a game whose repo was just read gets its pages remade from it
                game = db.get(Game, name)
                if store is not None and game is not None:
                    generate_for(db, store, game)
            done = make_missing_drafts(db, store)
            return done + approve_waiting(db)

    while True:
        try:
            await run_in_threadpool(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Making draft game pages failed")
        await asyncio.sleep(ROUND_SECONDS)
