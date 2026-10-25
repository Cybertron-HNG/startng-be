"""The category pages as the server writes them: /categories (all of them) and /categories/<slug> (one and its games).
nginx asks these routes (see startng-fe nginx.conf) and React does not start on the pages (src/main.tsx), so what search
engines read is what people see."""
import json

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session

from ... import flags
from ...db import get_db
from ...routers.games import _cover_url, _player_name
from ...routers.pages import GAME_PAGE_PARTS, INDIE_PAGE_PARTS, SHARE_IMAGE, SITEMAP_PROVIDERS, _get_shell, _page, _site, _title
from ...server_html import chrome, esc, game_card, tag_tile
from ...storage import game_url
from . import service
from .rules import GROUP_ORDER

router = APIRouter(tags=["tags"])
SLUG = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")


def _ld(data: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(data).replace("<", chr(92) + "u003c")}</script>'


def heading(label: str) -> str:
    """The category's name plus "games", unless it already ends with it ("Brainy games", "3D games", "Web games")."""
    return label if label.lower().endswith("games") else f"{label} games"


def _html(page: str, status: int = 200) -> Response:
    return Response(page, status_code=status, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def _not_found(shell: str, path: str) -> Response:
    body = chrome('<main class="container tags-page"><h1>That category isn\'t here</h1><p>There is no category with that name yet.</p><p><a href="/categories">See all categories</a> · <a href="/games">Browse all games</a></p></main>')
    return _html(_page(shell, title="Category not found · start.ng", description="There is no category with that name yet. Browse the games instead.", path=path, image=_site() + SHARE_IMAGE, body=body, noindex=True), 404)


@router.get("/pages/categories", response_class=Response, include_in_schema=False)
def categories_page(db: Session = Depends(get_db)):
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    site = _site()
    pairs = service.public_tags(db)
    groups = "".join(
        f'<div class="tags-group"><h2>{esc(g)}</h2><div class="tag-tiles">' + "".join(tag_tile(t.slug, t.label, n) for t, n in pairs if t.group_name == g) + "</div></div>"
        for g in GROUP_ORDER if any(t.group_name == g for t, _ in pairs)
    )
    inner = (
        '<main class="container tags-page"><p class="crumbs"><a href="/games">Games</a> › Categories</p>'
        '<h1>Browse games by category</h1>'
        '<p class="lead-sm">Pick how you want to play, or the mood you are in. One game can be in more than one category.</p>'
        f"{groups}</main>"
    )
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Games", "item": f"{site}/games"}, {"@type": "ListItem", "position": 2, "name": "Categories", "item": f"{site}/categories"}]}
    page = _page(shell, title="Browse web games by category · start.ng",
                 description="Browse free browser games on start.ng by category: endless runners, racing, brainy games, Nigerian themed games and more. Play in your browser, no download.",
                 path="/categories", image=site + SHARE_IMAGE, body=chrome(inner), extra_head=_ld(crumbs))
    return _html(page)


@router.get("/pages/categories/{slug}", response_class=Response, include_in_schema=False)
def category_page(slug: str = SLUG, page: int = Query(1, ge=1, le=1000), db: Session = Depends(get_db)):
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    path = f"/categories/{slug}"
    try:
        tag, total, games = service.games_of(db, slug, page)
    except service.TagError:
        return _not_found(shell, path)
    pages = max(1, -(-total // service.PER_PAGE))
    if page > pages:
        return _not_found(shell, path)
    page = next(n for n in range(1, pages + 1) if n == page)  # our own number, not the request's (it is one of these)

    site = _site()
    path = f"/categories/{tag.slug}"  # the stored slug, not the request's text
    h = heading(tag.label)
    href = lambda n: path + (f"?page={n}" if n > 1 else "")
    cards = "".join(game_card(g.name, _title(g.name), _player_name(g.owner, "a builder"), game_url(g.name).split("://", 1)[-1], _cover_url(g)) for g in games)
    related = [(t, n) for t, n in service.public_tags(db) if t.group_name == tag.group_name and t.slug != tag.slug and not t.everywhere][:4]
    pager = ""
    if pages > 1:
        prev = f'<a class="btn ghost" href="{esc(href(page - 1))}" rel="prev">← Previous</a>' if page > 1 else "<span></span>"
        nxt = f'<a class="btn ghost" href="{esc(href(page + 1))}" rel="next">Next →</a>' if page < pages else "<span></span>"
        pager = f'<nav class="tags-pager" aria-label="Pages of games">{prev}<span class="count">Page {esc(str(page))} of {pages}</span>{nxt}</nav>'
    more = ""
    if related:
        more = '<div class="tags-group"><h2>More like this</h2><div class="tag-tiles">' + "".join(tag_tile(t.slug, t.label, n) for t, n in related) + "</div></div>"
    inner = (
        f'<main class="container tags-page"><p class="crumbs"><a href="/games">Games</a> › <a href="/categories">Categories</a> › {esc(tag.label)}</p>'
        f'<p class="eyebrow">Category</p><h1>{esc(h)}</h1>'
        f'<p class="lead-sm">{esc(tag.blurb)}. Play free in your browser, no download. Every game has its own address and its own leaderboard.</p>'
        f'<div class="section-head"><h2>Games</h2><span class="count">{total} {"game" if total == 1 else "games"}</span></div>'
        f'<div class="grid">{cards}</div>{pager}{more}</main>'
    )
    description = f"{tag.blurb}. {total} {h.lower()} you can play free in your browser on start.ng."
    extra = [
        _ld({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Games", "item": f"{site}/games"},
            {"@type": "ListItem", "position": 2, "name": "Categories", "item": f"{site}/categories"},
            {"@type": "ListItem", "position": 3, "name": tag.label, "item": f"{site}{path}"}]}),
        _ld({"@context": "https://schema.org", "@type": "CollectionPage", "name": h, "description": description, "url": f"{site}{href(page)}",
             "mainEntity": {"@type": "ItemList", "numberOfItems": total, "itemListElement": [
                 {"@type": "ListItem", "position": (page - 1) * service.PER_PAGE + i + 1, "url": f"{site}/games/{g.name}", "name": _title(g.name)} for i, g in enumerate(games)]}}),
    ]
    html = _page(shell, title=f"{h}: play free in your browser · start.ng" + (f" (page {page})" if page > 1 else ""), description=description, path=href(page),
                 image=_cover_url(games[0]) if games and _cover_url(games[0]) else site + SHARE_IMAGE, body=chrome(inner), extra_head="\n    ".join(extra))
    return _html(html)


def sitemap_urls(db: Session) -> list[tuple[str, str | None]]:
    """The hub and every category that has a page. Only the first page of each: the others are reached from it."""
    if not flags.is_on("tags"):
        return []  # the routes answer 404 while the flag is off, so the sitemap must not list them
    return [("/categories", None)] + [(f"/categories/{t.slug}", None) for t, _ in service.public_tags(db)]


SITEMAP_PROVIDERS.append(sitemap_urls)


def game_page_part(db: Session, game) -> str:
    """The "Categories" links on a game's server-written page: the same words and classes as src/features/tags/GameTags.tsx."""
    if not flags.is_on("tags"):
        return ""
    links = service.links_for(db, game.name)
    if not links:
        return ""
    items = "".join(f'<li><a href="/categories/{esc(s)}">{esc(l)}</a></li>' for s, l in links)
    return f'<section class="game-tags" aria-label="Categories"><p class="eyebrow">Categories</p><ul>{items}</ul></section>'


GAME_PAGE_PARTS.append(game_page_part)


def indie_page_part(db: Session, game) -> str:
    """The categories of an independent creator's game, on its own page only (not links: these games are not on the category pages)."""
    if not flags.is_on("tags"):
        return ""
    labels = service.indie_labels(db, game.id)
    if not labels:
        return ""
    items = "".join(f"<li>{esc(l)}</li>" for l in labels)
    return f'<section class="game-tags" aria-label="Categories"><p class="eyebrow">Categories</p><ul class="plain">{items}</ul></section>'


INDIE_PAGE_PARTS.append(indie_page_part)
