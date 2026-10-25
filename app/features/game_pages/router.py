import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ...activity import record
from ...admin import AdminUser, require
from ...db import get_db
from ...game_store import get_game_store
from ...models import Game
from ...routers.games import _cover_url, _player_name
from ...routers.games import leaderboard as game_leaderboard
from ...routers.pages import SHARE_IMAGE, _esc, _get_shell, _page, _site, _title
from ...server_html import chrome, esc, game_card
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


def _stat(stat: dict, value) -> str:
    if value is None:
        return "–"
    return f"{value // 60}:{value % 60:02d}" if stat.get("format") == "time" else f"{value:,}"


def _top_players(db: Session, game: Game, kind: str) -> str:
    """The best players right now, read live (so it is never out of date). Only for a game that has a leaderboard."""
    if kind not in ("about", "how-to-play") or not game.leaderboard:
        return ""
    try:
        entries = game_leaderboard(game.name, 5, db)
    except Exception:  # a leaderboard that can't be read is left out, never an error page
        return ""
    if not entries:
        return ""
    stats = [c for c in game.leaderboard if isinstance(c, dict) and c.get("key")]
    def score_line(e) -> str:
        return " · ".join("{} {}".format(c.get("label") or c["key"], _stat(c, e.stats.get(c["key"]))) for c in stats)

    rows = "".join(f"<li>{e.rank}. <b>{_esc(e.name)}</b> <span>{_esc(score_line(e))}</span></li>" for e in entries)
    return (
        f'<section class="game-sub-links game-sub-top" aria-label="Top players"><h2>Top players right now</h2><ul>{rows}</ul>'
        f'<p><a href="/games/{esc(game.name)}">See the full leaderboard</a></p></section>'
    )


def _cards(games: list[Game]) -> str:
    return "".join(game_card(g.name, _title(g.name), _player_name(g.owner, "a builder"), game_url(g.name).split("://", 1)[-1], _cover_url(g)) for g in games)


def _more_games(db: Session, game: Game, by: str) -> str:
    """More games by the same creator, then games in the same category: real links to other pages on start.ng, read live."""
    sections = []
    seen = {game.name}
    mine = db.scalars(
        select(Game).options(selectinload(Game.owner)).where(Game.owner_id == game.owner_id, Game.name != game.name, Game.hidden.is_(None))
        .order_by(Game.updated_at.desc()).limit(4)
    ).all()
    if mine:
        seen |= {g.name for g in mine}
        sections.append(f'<section class="game-sub-more" aria-label="More games by {esc(by)}"><div class="section-head"><h2>More games by {esc(by)}</h2></div><div class="grid">{_cards(list(mine))}</div></section>')
    similar = _similar_games(db, game, seen)
    if similar:
        sections.append(f'<section class="game-sub-more" aria-label="Similar games"><div class="section-head"><h2>Games like {esc(_title(game.name))}</h2></div><div class="grid">{_cards(similar)}</div></section>')
    return "".join(sections)


def _similar_games(db: Session, game: Game, seen: set[str]) -> list[Game]:
    """Games in the first category this game shares with others (not the four "words people search" ones that every game has)."""
    from ... import flags
    if not flags.is_on("tags"):
        return []
    from ..tags import service as tags
    for tag in tags.tags_of(db, game.name):
        if tag.everywhere:
            continue
        try:
            _, _, games = tags.games_of(db, tag.slug)
        except tags.TagError:
            continue  # a category with too few games has no public page
        found = [g for g in games if g.name not in seen][:4]
        if found:
            return found
    return []


@router.get("/pages/games/{name}/{kind}", response_class=Response, include_in_schema=False)
def game_sub_page(name: str = GAME, kind: str = KIND, db: Session = Depends(get_db)):
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    game = db.get(Game, name)
    page = service.approved_page(db, name, kind) if game and not game.hidden else None
    if page is None:
        return _not_found(shell, name, kind)

    kind = next(k for k in gen.KINDS if k == kind)  # our own word for it, not the request's text (KIND only lets these through)
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
        f"{_top_players(db, game, kind)}"
        f'<nav class="game-sub-links" aria-label="More about {_esc(title)}"><h2>More about {_esc(title)}</h2><ul>{others}<li><a href="/games/{game.name}">{_esc(title)} leaderboard and details</a></li><li><a href="/categories">Browse games by category</a></li></ul></nav>'
        f"{categories}{_more_games(db, game, by)}</main>"
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
