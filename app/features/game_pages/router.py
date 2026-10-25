import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...activity import record
from ...admin import AdminUser, require
from ...db import get_db
from ...game_store import get_game_store
from ...models import Game
from ...routers.games import _cover_url, _player_name
from ...routers.pages import SHARE_IMAGE, _esc, _get_shell, _page, _site, _title
from ...server_html import chrome
from ...storage import game_url
from . import generate as gen
from . import service

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(tags=["game pages"])

GAME = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
KIND = Path(pattern=r"^(about|how-to-play|faq|walkthrough)$")
manage = require("gamepages.manage")


def _ld(data: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(data).replace("<", chr(92) + "u003c")}</script>'


def _not_found(shell: str, name: str, kind: str) -> Response:
    body = chrome('<main class="container game-sub"><h1>Page not found</h1><p>There is no page like that on start.ng.</p><p><a href="/games">Browse all games</a></p></main>')
    page = _page(shell, title="Page not found · start.ng", description="There is no page like that on start.ng. Browse the games instead.", path=f"/games/{name}/{kind}", image=_site() + SHARE_IMAGE, body=body, noindex=True)
    return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def _category_links(db: Session, name: str) -> list[tuple[str, str]]:
    """The game's categories that have a page, when the tags feature is on: its own pages link to them too."""
    from ... import flags
    if not flags.is_on("tags"):
        return []
    from ..tags import service as tags
    return tags.links_for(db, name)


@router.get("/pages/games/{name}/{kind}", response_class=Response, include_in_schema=False)
def game_sub_page(name: str = GAME, kind: str = KIND, db: Session = Depends(get_db)):
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    game = db.get(Game, name)
    page = service.approved_page(db, name, kind) if game and not game.hidden else None
    if page is None:
        return _not_found(shell, name, kind)

    title = _title(game.name)
    path = f"/games/{game.name}/{kind}"
    site = _site()
    image = _cover_url(game) or site + SHARE_IMAGE
    kinds = service.approved_kinds(db, game.name)
    by = _player_name(game.owner, "a builder")
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Games", "item": f"{site}/games"},
        {"@type": "ListItem", "position": 2, "name": title, "item": f"{site}/games/{game.name}"},
        {"@type": "ListItem", "position": 3, "name": gen.SHORT[kind], "item": f"{site}{path}"},
    ]}
    extra = [_ld(crumbs)]
    if kind == "faq":
        pairs = gen.faq_pairs(page.body)
        if pairs:
            extra.append(_ld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs]}))
    elif kind == "how-to-play":
        steps = gen.ordered_steps(page.body)
        if steps:
            extra.append(_ld({"@context": "https://schema.org", "@type": "HowTo", "name": page.title, "step": [{"@type": "HowToStep", "text": s} for s in steps]}))
    elif kind == "about":
        extra.append(_ld({"@context": "https://schema.org", "@type": "VideoGame", "name": title, "description": page.description, "url": f"{site}{path}",
                          "image": image, "author": {"@type": "Person", "name": by}, "gamePlatform": "Web browser", "applicationCategory": "Game",
                          "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}))

    cats = _category_links(db, game.name)
    categories = (
        '<div class="game-tags" aria-label="Categories"><p class="eyebrow">Categories</p><ul>'
        + "".join(f'<li><a href="/categories/{_esc(s)}">{_esc(l)}</a></li>' for s, l in cats) + "</ul></div>"
    ) if cats else ""
    others = "".join(f'<li><a href="/games/{game.name}/{k}">{_esc(gen.LABELS[k].format(t=title))}</a></li>' for k in kinds if k != kind)
    inner = (
        '<main class="container game-sub">'
        f'<p class="crumbs"><a href="/games">Games</a> › <a href="/games/{game.name}">{_esc(title)}</a> › {_esc(gen.SHORT[kind])}</p>'
        f'<p class="eyebrow">{_esc(title)}</p><h1>{_esc(page.title)}</h1>'
        f'<div class="game-sub-body">{gen.to_html(page.body)}</div>'
        f'<p class="game-sub-play"><a class="btn" href="{_esc(game_url(game.name))}">Play {_esc(title)}</a> <a class="btn ghost" href="/games/{game.name}">{_esc(title)} on start.ng</a></p>'
        f'<nav class="game-sub-links" aria-label="More about {_esc(title)}"><h2>More about {_esc(title)}</h2><ul>{others}<li><a href="/games/{game.name}">{_esc(title)} leaderboard and details</a></li><li><a href="/categories">Browse games by category</a></li></ul></nav>'
        f"{categories}</main>"
    )
    html = _page(shell, title=f"{page.title} · start.ng", description=page.description or f"{page.title} on start.ng", path=path, image=image, body=chrome(inner), extra_head="\n    ".join(extra))
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


class PagesOut(BaseModel):
    kind: str
    label: str
    path: str


@router.get("/games/{name}/pages", response_model=list[PagesOut])
def game_pages(name: str = GAME, db: Session = Depends(get_db)):
    """The approved pages of a game, so its page can link to them."""
    game = db.get(Game, name)
    if game is None or game.hidden:
        return []
    title = _title(name)
    return [PagesOut(kind=k, label=gen.LABELS[k].format(t=title), path=f"/games/{name}/{k}") for k in service.approved_kinds(db, name)]


# ---- admin

class PageRow(BaseModel):
    id: int
    game: str
    kind: str
    status: str
    title: str
    reason: str | None
    edited: bool
    generated_at: datetime


class PageFull(PageRow):
    description: str
    body: str
    facts: dict | None


class EditBody(BaseModel):
    title: str
    description: str = ""
    body: str


class GenerateBody(BaseModel):
    game: str | None = None
    force: bool = False


def _row(p) -> PageRow:
    return PageRow(id=p.id, game=p.game_name, kind=p.kind, status=p.status, title=p.title, reason=p.reason, edited=p.edited, generated_at=p.generated_at)


def _full(p) -> PageFull:
    return PageFull(**_row(p).model_dump(), description=p.description, body=p.body, facts=p.facts)


def _call(fn, *a):
    try:
        return fn(*a)
    except service.PageError as e:
        raise HTTPException(e.status, e.message)


@router.get("/admin/game-pages", response_model=list[PageRow])
def admin_list(status: str = Query("draft", pattern="^(draft|approved|skipped|all)$"), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    return [_row(p) for p in service.queue(db, status, limit, offset)]


@router.get("/admin/game-pages/summary")
def admin_summary(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    c = service.counts(db)
    return {"draft": c.get("draft", 0), "approved": c.get("approved", 0), "skipped": c.get("skipped", 0), "games_without_pages": len(service.gaps(db))}


@router.get("/admin/game-pages/gaps")
def admin_gaps(admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Games that have no page, and why: the list to send creators so they add a description."""
    return service.gaps(db)


@router.get("/admin/game-pages/{page_id}", response_model=PageFull)
def admin_get(page_id: int, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    return _full(_call(service.get_page, db, page_id))


@router.put("/admin/game-pages/{page_id}", response_model=PageFull)
def admin_edit(page_id: int, body: EditBody, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    page = _call(service.set_text, db, page_id, body.title, body.description, body.body)
    record(db, admin.user, "admin", "game_pages.edited", page.game_name, page=page.kind)
    return _full(page)


@router.post("/admin/game-pages/{page_id}/approve", response_model=PageRow)
def admin_approve(page_id: int, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    page = _call(service.approve, db, page_id, admin.user.id)
    record(db, admin.user, "admin", "game_pages.approved", page.game_name, page=page.kind)
    return _row(page)


@router.post("/admin/game-pages/{page_id}/unapprove", response_model=PageRow)
def admin_unapprove(page_id: int, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    page = _call(service.unapprove, db, page_id)
    record(db, admin.user, "admin", "game_pages.unapproved", page.game_name, page=page.kind)
    return _row(page)


@router.post("/admin/game-pages/generate")
def admin_generate(body: GenerateBody, admin: AdminUser = Depends(manage), db: Session = Depends(get_db)):
    """Make draft pages from the games' own files. Approved pages and edited pages are kept unless force."""
    store = get_game_store()
    if store is None:
        raise HTTPException(503, "The games' files are not reachable from here, so there is nothing to write pages from")
    if body.game:
        game = db.get(Game, body.game)
        if game is None:
            raise HTTPException(404, "No such game")
        done = service.generate_for(db, store, game, force=body.force)
        counts = {s: sum(1 for v in done.values() if v == s) for s in ("draft", "approved", "skipped")}
    else:
        counts = service.generate_all(db, store, force=body.force)
    record(db, admin.user, "admin", "game_pages.generated", body.game, **counts)
    return counts


# the sitemap lists every approved page (the list is in app/routers/pages.py)
from ...routers.pages import SITEMAP_PROVIDERS  # noqa: E402

SITEMAP_PROVIDERS.append(service.sitemap_urls)


def _game_page_part(db: Session, game) -> str:
    """The "More about this game" links on a game's server-written page: the same words and classes as src/features/game_pages/GamePages.tsx."""
    from ... import flags
    if not flags.is_on("game_pages"):
        return ""
    title = _title(game.name)
    items = "".join(f'<li><a href="/games/{_esc(game.name)}/{k}">{_esc(gen.LABELS[k].format(t=title))}</a></li>' for k in service.approved_kinds(db, game.name))
    return f'<section class="game-tags" aria-label="More about this game"><p class="eyebrow">More about this game</p><ul>{items}</ul></section>' if items else ""


from ...routers.pages import GAME_PAGE_PARTS  # noqa: E402

GAME_PAGE_PARTS.append(_game_page_part)
