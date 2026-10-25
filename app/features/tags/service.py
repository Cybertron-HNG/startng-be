"""The contract other features and pages use: which tags a game has, which games a tag has, and the review steps.
Only "approved" tags are shown to players. The rules in rules.py only suggest."""
import asyncio
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import flags
from ...config import settings
from ...db import SessionLocal
from ...models import Game
from .models import GameTag, Tag
from .rules import GROUPS, GROUP_ORDER, MIN_GAMES

log = logging.getLogger(__name__)
PER_PAGE = 24
# Tags that say something about a place or a people. The rules over-match them (a word like "market" is not Nigerian), and the brief wants
# those pages only for games that truly fit, so they are never approved by themselves: a person confirms them in the admin.
NEEDS_A_PERSON = {"nigerian-themed", "african-themed", "lagos"}
_RULES = {slug: re.compile(rx) for tags in GROUPS.values() for slug, _label, _blurb, rx in tags if rx}


class TagError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def ensure_tags(db: Session) -> dict[str, Tag]:
    """Make sure every tag in rules.py exists (and its words are current). Safe to call again and again."""
    have = {t.slug: t for t in db.scalars(select(Tag)).all()}
    changed = False
    for group in GROUP_ORDER:
        for position, (slug, label, blurb, rx) in enumerate(GROUPS[group]):
            t = have.get(slug)
            if t is None:
                t = have[slug] = Tag(slug=slug, label=label, group_name=group, blurb=blurb, position=position, everywhere=rx is None)
                db.add(t)
                changed = True
            elif (t.label, t.group_name, t.blurb, t.position, t.everywhere) != (label, group, blurb, position, rx is None):
                t.label, t.group_name, t.blurb, t.position, t.everywhere = label, group, blurb, position, rx is None
                changed = True
    if changed:
        db.commit()
    return have


def _visible_games():
    return select(Game).where(Game.hidden.is_(None))


def suggest_for(name: str, description: str | None) -> list[str]:
    """The tags the rules would suggest for a game, from its name and description."""
    text = f"{name.replace('-', ' ')} {description or ''}".lower()
    return [slug for slug, rx in _RULES.items() if rx.search(text)]


def suggest_all(db: Session) -> int:
    """Add a suggestion for every visible game and every tag the rules match. Never changes an existing row (so
    approved and rejected tags stay as an admin left them). Returns how many suggestions were added."""
    tags = ensure_tags(db)
    existing = {(g, t) for g, t in db.execute(select(GameTag.game_name, GameTag.tag_id)).all()}
    added = 0
    for game in db.scalars(_visible_games()).all():
        for slug in suggest_for(game.name, game.description):
            tag = tags[slug]
            if (game.name, tag.id) not in existing:
                db.add(GameTag(game_name=game.name, tag_id=tag.id, status="suggested"))
                added += 1
    db.commit()
    return added


def suggest_game(db: Session, game_name: str) -> int:
    """Add the rules' suggestions for one game. Never changes an existing row, so what a person approved or rejected stays.
    Returns how many were added."""
    game = db.get(Game, game_name)
    if game is None or game.hidden is not None:
        return 0
    tags = ensure_tags(db)
    have = set(db.scalars(select(GameTag.tag_id).where(GameTag.game_name == game_name)).all())
    added = 0
    for slug in suggest_for(game.name, game.description):
        if tags[slug].id not in have:
            db.add(GameTag(game_name=game_name, tag_id=tags[slug].id, status="suggested"))
            added += 1
    if added:
        db.commit()
    return added


def auto_approve_game(db: Session, game_name: str) -> list[str]:
    """Approve the suggestions on a game that need no person (everything except NEEDS_A_PERSON). A tag an admin rejected is
    never brought back. Returns the slugs approved now."""
    if not settings.tags_auto_approve:
        return []
    tags = {t.id: t for t in ensure_tags(db).values()}
    now = datetime.now(timezone.utc)
    done = []
    for row in db.scalars(select(GameTag).where(GameTag.game_name == game_name, GameTag.status == "suggested")).all():
        tag = tags.get(row.tag_id)
        if tag is not None and not tag.everywhere and tag.slug not in NEEDS_A_PERSON:
            row.status, row.approved_at = "approved", now
            done.append(tag.slug)
    if done:
        db.commit()
    return done


def refresh_game(db: Session, game_name: str) -> list[str]:
    """What happens to a game when it is new or has changed: suggest tags for it, and approve the ones that need no person."""
    suggest_game(db, game_name)
    return auto_approve_game(db, game_name)


AUTO_PER_ROUND = 50
ROUND_SECONDS = 120


def refresh_missing(db: Session, limit: int = AUTO_PER_ROUND) -> int:
    """Games that have never had any tag row (a new game, or every existing game the first time): suggest and approve. A
    game the rules match nothing for gets no row, so it is looked at again each round: that is cheap (words only)."""
    have = set(db.scalars(select(GameTag.game_name).distinct()).all())
    todo = [g.name for g in db.scalars(_visible_games().order_by(Game.created_at.desc())).all() if g.name not in have][:limit]
    for name in todo:
        refresh_game(db, name)
    return len(todo)


def on_game_changed(payload: dict) -> None:
    """A game was published or got a new version: its words may have changed, so look again. Subscribed in __init__.py."""
    if not flags.is_on("tags"):
        return
    with SessionLocal() as db:
        refresh_game(db, payload["game"])


async def run_forever() -> None:
    """The background job, started with the server; does nothing while the flag is off."""
    from starlette.concurrency import run_in_threadpool

    def tick() -> int:
        if not flags.is_on("tags"):
            return 0
        with SessionLocal() as db:
            return refresh_missing(db)

    while True:
        try:
            await run_in_threadpool(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Suggesting tags failed")
        await asyncio.sleep(ROUND_SECONDS)


def approve(db: Session, game_name: str, slugs: list[str]) -> list[str]:
    """Set exactly these tags as approved on a game. A tag that was suggested or approved and is not in the list
    becomes rejected, so it does not come back as a suggestion. Returns the approved slugs."""
    if db.get(Game, game_name) is None:
        raise TagError("No such game", 404)
    tags = ensure_tags(db)
    wanted = list(dict.fromkeys(slugs))
    unknown = [s for s in wanted if s not in tags]
    if unknown:
        raise TagError(f"Unknown tag: {unknown[0]}")
    if any(tags[s].everywhere for s in wanted):
        wanted = [s for s in wanted if not tags[s].everywhere]  # "Web games" and the like are on every game already
    ids = {tags[s].id: s for s in wanted}
    now = datetime.now(timezone.utc)
    rows = {r.tag_id: r for r in db.scalars(select(GameTag).where(GameTag.game_name == game_name)).all()}
    for tag_id, row in rows.items():
        if tag_id in ids:
            if row.status != "approved":
                row.status, row.approved_at = "approved", now
        elif row.status != "rejected":
            row.status, row.approved_at = "rejected", None
    for tag_id in ids:
        if tag_id not in rows:
            db.add(GameTag(game_name=game_name, tag_id=tag_id, status="approved", approved_at=now))
    db.commit()
    return wanted


def tags_of(db: Session, game_name: str) -> list[Tag]:
    """The approved tags of a game, plus the ones every game has, in the order of the tag list."""
    ensure_tags(db)
    approved = select(GameTag.tag_id).where(GameTag.game_name == game_name, GameTag.status == "approved")
    rows = db.scalars(select(Tag).where((Tag.everywhere.is_(True)) | (Tag.id.in_(approved)))).all()
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(rows, key=lambda t: (order.get(t.group_name, 99), t.position))


def counts(db: Session) -> dict[str, int]:
    """slug -> how many visible games have it (approved only; "everywhere" tags count every visible game)."""
    tags = ensure_tags(db)
    total = db.scalar(select(func.count()).select_from(_visible_games().subquery())) or 0
    rows = db.execute(
        select(GameTag.tag_id, func.count()).join(Game, Game.name == GameTag.game_name)
        .where(GameTag.status == "approved", Game.hidden.is_(None)).group_by(GameTag.tag_id)
    ).all()
    by_id = dict(rows)
    return {slug: (total if t.everywhere else by_id.get(t.id, 0)) for slug, t in tags.items()}


def public_tags(db: Session) -> list[tuple[Tag, int]]:
    """Tags that get a public page (MIN_GAMES or more games), in list order."""
    have = counts(db)
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    tags = [t for t in ensure_tags(db).values() if have[t.slug] >= MIN_GAMES]
    tags.sort(key=lambda t: (order.get(t.group_name, 99), t.position))
    return [(t, have[t.slug]) for t in tags]


def links_for(db: Session, game_name: str) -> list[tuple[str, str]]:
    """(slug, label) of a game's approved tags that have a public page: what its page links to."""
    public = {t.slug for t, _ in public_tags(db)}
    return [(t.slug, t.label) for t in tags_of(db, game_name) if t.slug in public]


def games_of(db: Session, slug: str, page: int = 1) -> tuple[Tag, int, list[Game]]:
    """(tag, total games, the games on this page, newest first). Raises TagError(404) for an unknown tag or one that
    does not have a public page yet."""
    tag = ensure_tags(db).get(slug)
    total = counts(db).get(slug, 0) if tag else 0
    if tag is None or total < MIN_GAMES:
        raise TagError("No such category", 404)
    q = _visible_games().order_by(Game.updated_at.desc())
    if not tag.everywhere:
        q = q.where(Game.name.in_(select(GameTag.game_name).where(GameTag.tag_id == tag.id, GameTag.status == "approved")))
    games = db.scalars(q.limit(PER_PAGE).offset((max(page, 1) - 1) * PER_PAGE)).all()
    return tag, total, list(games)


def review_queue(db: Session, pending_only: bool, limit: int, offset: int) -> list[dict]:
    """Games for the admin to review: each with its approved and its suggested tag slugs."""
    tags = {t.id: t.slug for t in ensure_tags(db).values()}
    by_game: dict[str, dict[str, list[str]]] = {}
    for g, tag_id, status in db.execute(select(GameTag.game_name, GameTag.tag_id, GameTag.status)).all():
        by_game.setdefault(g, {"approved": [], "suggested": []})
        if status in ("approved", "suggested"):
            by_game[g][status].append(tags[tag_id])
    games = db.scalars(_visible_games().order_by(Game.name)).all()
    rows = [{"game": g.name, "approved": by_game.get(g.name, {}).get("approved", []), "suggested": by_game.get(g.name, {}).get("suggested", [])} for g in games]
    if pending_only:
        rows = [r for r in rows if r["suggested"]]
    return rows[offset: offset + limit]
