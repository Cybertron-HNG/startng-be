"""Making, reviewing and reading the pages about games. The server pages and the admin both go through here."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import Game
from . import generate as gen
from .facts import read_facts
from .models import GamePage

SIMILAR = 0.85  # two pages this alike are the same page: only the first is made


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
        if row is not None and (row.status == "approved" or row.edited) and not force:
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
    db.commit()
    return page


def approve(db: Session, page_id: int, admin_user_id: int) -> GamePage:
    page = get_page(db, page_id)
    if page.status == "skipped":
        raise PageError("This page was not made, so it can't be approved.", 409)
    page.status, page.approved_at, page.approved_by_id = "approved", datetime.now(timezone.utc), admin_user_id
    db.commit()
    return page


def unapprove(db: Session, page_id: int) -> GamePage:
    page = get_page(db, page_id)
    if page.status == "approved":
        page.status, page.approved_at, page.approved_by_id = "draft", None, None
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
