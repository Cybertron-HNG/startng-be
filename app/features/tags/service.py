"""The contract other features and pages use: which tags a game has, which games a tag has, and the review steps.
Only "approved" tags are shown to players. The rules in rules.py only suggest."""
import asyncio
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import flags, manifest
from ...config import settings
from ...db import SessionLocal
from ...models import Game
from .models import GameTag, Tag, TagRequest
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


def _decided_by_a_person(db: Session, game_name: str) -> bool:
    """The game's creator, a review or an admin already chose its tags: the rules leave it alone."""
    return db.scalar(select(GameTag.id).where(GameTag.game_name == game_name, GameTag.source != "rules").limit(1)) is not None


def suggest_game(db: Session, game_name: str) -> int:
    """Add the rules' suggestions for one game. Never changes an existing row, so what a person approved or rejected stays, and a
    game whose tags someone chose gets none. Returns how many were added."""
    game = db.get(Game, game_name)
    if game is None or game.hidden is not None or _decided_by_a_person(db, game_name):
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


REVIEWED_FILE = Path(__file__).with_name("reviewed.json")


@lru_cache(maxsize=1)
def reviewed() -> dict[str, list[str]]:
    """The tags start.ng chose, by reading each game, for the games that were live before creators could pick their own
    (reviewed.json: game name -> category slugs). Only used for a game nobody else has chosen tags for."""
    try:
        data = json.loads(REVIEWED_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return {g: [s for s in slugs if s in _CHOOSABLE] for g, slugs in data.items() if isinstance(slugs, list)}


def refresh_game(db: Session, game_name: str) -> list[str]:
    """What happens to a game when it is new or has changed: use the tags chosen for it (by the review, or by its creator), else
    suggest tags with the rules, and approve the ones that need no person."""
    chosen = reviewed().get(game_name)
    if chosen and not _decided_by_a_person(db, game_name) and db.get(Game, game_name) is not None:
        db.execute(delete(GameTag).where(GameTag.game_name == game_name, GameTag.source == "rules"))
        db.flush()
        return approve(db, game_name, chosen, source="review")
    suggest_game(db, game_name)
    return auto_approve_game(db, game_name)


AUTO_PER_ROUND = 50
ROUND_SECONDS = 120


def refresh_missing(db: Session, limit: int = AUTO_PER_ROUND) -> int:
    """Games that have never had any tag row (a new game, or every existing game the first time): suggest and approve. A
    game the rules match nothing for gets no row, so it is looked at again each round: that is cheap (words only)."""
    have = set(db.scalars(select(GameTag.game_name).distinct()).all())
    decided = set(db.scalars(select(GameTag.game_name).where(GameTag.source != "rules").distinct()).all())
    chosen = reviewed()
    todo = [g.name for g in db.scalars(_visible_games().order_by(Game.created_at.desc())).all()
            if g.name not in have or (g.name in chosen and g.name not in decided)][:limit]
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


def approve(db: Session, game_name: str, slugs: list[str], source: str = "admin") -> list[str]:
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
            row.source = source
        elif row.status != "rejected":
            row.status, row.approved_at, row.source = "rejected", None, source
    for tag_id in ids:
        if tag_id not in rows:
            db.add(GameTag(game_name=game_name, tag_id=tag_id, status="approved", approved_at=now, source=source))
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
    asked = requests_by_game(db)
    rows = [{"game": g.name, "approved": by_game.get(g.name, {}).get("approved", []), "suggested": by_game.get(g.name, {}).get("suggested", []),
             "requested": asked.get(g.name, [])} for g in games]
    if pending_only:
        rows = [r for r in rows if r["suggested"] or r["requested"]]
    return rows[offset: offset + limit]


# ---- the creator's own choice, in startng.json:  {"categories": ["endless-runners", "3d-games"], "other_category": "Rhythm"}

CREATOR_MAX = 5
_CHOOSABLE = {slug: label for tags in ([t for g, t in GROUPS.items() if g != "Words people search"]) for slug, label, _b, _r in tags}
_BY_LABEL = {label.lower(): slug for slug, label in _CHOOSABLE.items()}


def parse_categories(value) -> list[str]:
    """The categories a creator picked, as slugs. A slug or the name as shown on start.ng both work. Anything else refuses the publish,
    with the list to pick from, so a typo is found now and not silently dropped."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise manifest.SchemaError('startng.json: "categories" must be a list of category names, e.g. ["endless-runners", "3d-games"]')
    picked: list[str] = []
    for v in value:
        key = v.strip().lower()
        dashed = key.replace(" ", "-")
        slug = key if key in _CHOOSABLE else dashed if dashed in _CHOOSABLE else _BY_LABEL.get(key)
        if not slug:
            raise manifest.SchemaError(f'startng.json: "{v}" is not a start.ng category. Pick from: {", ".join(_CHOOSABLE)}. If none fit, put your own in "other_category".')
        if slug not in picked:
            picked.append(slug)
    if len(picked) > CREATOR_MAX:
        raise manifest.SchemaError(f'startng.json: pick up to {CREATOR_MAX} categories (you listed {len(picked)}).')
    return picked


def parse_other(value) -> str | None:
    """The category a creator wrote because none of ours fit: plain text, one line, up to 40 characters. Never an error."""
    from ...leaderboard import clean_description

    text = clean_description(value) if isinstance(value, str) else None
    return text[:40] if text else None


def apply_categories(db: Session, game: Game, slugs: list[str]) -> None:
    """Save the creator's choice as this game's tags. It replaces what the rules suggested, but not what an admin decided."""
    if not slugs:
        return
    if db.scalar(select(GameTag.id).where(GameTag.game_name == game.name, GameTag.source.in_(("admin", "review"))).limit(1)) is not None:
        return  # an admin or the review already set this game's tags: the creator's list does not overwrite it
    # drop the rules' guesses, keep the creator's list as the approved tags
    db.execute(delete(GameTag).where(GameTag.game_name == game.name, GameTag.source == "rules"))
    db.flush()
    approve(db, game.name, slugs, source="creator")


def apply_other(db: Session, game: Game, text: str | None) -> None:
    if text and db.scalar(select(TagRequest.id).where(TagRequest.game_name == game.name, TagRequest.text == text)) is None:
        db.add(TagRequest(game_name=game.name, text=text))
        db.flush()


def requests_by_game(db: Session) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for g, text in db.execute(select(TagRequest.game_name, TagRequest.text).order_by(TagRequest.created_at)).all():
        out.setdefault(g, []).append(text)
    return out


manifest.register_section("categories", parse_categories, apply_categories)
manifest.register_section("other_category", parse_other, apply_other)
