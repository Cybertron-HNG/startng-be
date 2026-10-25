from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...activity import record
from ...admin import AdminUser, require
from ...db import get_db
from ...routers.games import _game_out, _recent_plays
from ...schemas import GameOut
from . import server_pages, service
from .rules import GROUP_ORDER

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["tags"])
router.include_router(server_pages.router)  # /pages/categories and /pages/categories/<slug>: the pages as the server writes them

SLUG = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
GAME = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
manage = require("tags.manage")


class TagOut(BaseModel):
    slug: str
    label: str
    blurb: str
    group: str
    games: int


class GroupOut(BaseModel):
    name: str
    tags: list[TagOut]


class TagsOut(BaseModel):
    total_games: int
    groups: list[GroupOut]


class TagPageOut(BaseModel):
    tag: TagOut
    games: list[GameOut]
    page: int
    pages: int
    related: list[TagOut]  # the other tags in the same group


class GameTagOut(BaseModel):
    slug: str
    label: str


def _out(tag, games: int) -> TagOut:
    return TagOut(slug=tag.slug, label=tag.label, blurb=tag.blurb, group=tag.group_name, games=games)


@router.get("/tags", response_model=TagsOut)
def list_tags(db: Session = Depends(get_db)):
    """Every tag that has a page (at least MIN_GAMES games), in groups, with how many games each has."""
    pairs = service.public_tags(db)
    groups = [GroupOut(name=g, tags=[_out(t, n) for t, n in pairs if t.group_name == g]) for g in GROUP_ORDER]
    total = max((n for t, n in pairs if t.everywhere), default=0)
    return TagsOut(total_games=total, groups=[g for g in groups if g.tags])


@router.get("/tags/{slug}", response_model=TagPageOut)
def tag_page(slug: str = SLUG, page: int = Query(1, ge=1, le=1000), db: Session = Depends(get_db)):
    try:
        tag, total, games = service.games_of(db, slug, page)
    except service.TagError as e:
        raise HTTPException(e.status, e.message)
    plays = _recent_plays(db)
    related = [_out(t, n) for t, n in service.public_tags(db) if t.group_name == tag.group_name and t.slug != tag.slug and not t.everywhere]
    return TagPageOut(
        tag=_out(tag, total), games=[_game_out(g, plays=plays.get(g.name, 0)) for g in games],
        page=page, pages=max(1, -(-total // service.PER_PAGE)), related=related,
    )


@router.get("/games/{name}/tags", response_model=list[GameTagOut])
def game_tags(name: str = GAME, db: Session = Depends(get_db)):
    """The approved tags of one game that have a public page, so its page can link to them."""
    return [GameTagOut(slug=s, label=l) for s, l in service.links_for(db, name)]


# ---- admin: suggest, review, approve

class ReviewRow(BaseModel):
    game: str
    approved: list[str]
    suggested: list[str]


class SetTags(BaseModel):
    tags: list[str]


@router.get("/admin/tags", response_model=list[TagOut])
def admin_tags(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Every tag, including the ones that do not have a public page yet (fewer than MIN_GAMES games)."""
    have = service.counts(db)
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    tags = sorted(service.ensure_tags(db).values(), key=lambda t: (order.get(t.group_name, 99), t.position))
    return [_out(t, have[t.slug]) for t in tags]


@router.get("/admin/tags/review", response_model=list[ReviewRow])
def review(pending: bool = True, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
           admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    return [ReviewRow(**r) for r in service.review_queue(db, pending, limit, offset)]


@router.post("/admin/tags/suggest")
def suggest(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Run the rules over every visible game. Adds suggestions only; nothing becomes public until it is approved."""
    added = service.suggest_all(db)
    record(db, admin.user, "admin", "tags.suggested", None, added=added)
    return {"added": added}


@router.put("/admin/games/{name}/tags", response_model=list[str])
def set_tags(body: SetTags, name: str = GAME, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    try:
        approved = service.approve(db, name, body.tags)
    except service.TagError as e:
        raise HTTPException(e.status, e.message)
    record(db, admin.user, "admin", "tags.set", name, tags=approved)
    return approved
