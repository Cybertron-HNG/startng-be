"""Pages for search engines and link previews.

The website is a client-side app, so crawlers (and chat apps making a link preview) get an empty page.
For a game's page, start.ng/games/<name>, nginx on the website asks this router for the page instead:
it is the website's own HTML with that game's title, description and preview image already in it,
plus a plain-text copy of the page inside #root. The browser then loads the app as usual and replaces
that text. If this router isn't reachable, nginx falls back to the plain page.

Also the sitemap, which lists every game (the site's fixed pages and the games from the database).
"""
import html
import json
import logging
import re
import time
from datetime import timezone

import requests
from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, defer, selectinload

from ..config import settings
from ..db import get_db
from ..models import Game, IndieGame
from ..storage import game_url
from .games import _cover_url, _player_name

log = logging.getLogger(__name__)
router = APIRouter(prefix="/pages", tags=["pages"], include_in_schema=False)

SEO_BLOCK = re.compile(r"<!--seo-->[\s\S]*?<!--/seo-->")
ROOT = '<div id="root"></div>'
SHARE_IMAGE = "/hangout-cover.jpg"  # used when a game has no cover
SHELL_SECONDS = 300
FIXED_PAGES = ["/", "/games", "/creators", "/tournament", "/leaderboard", "/hangout", "/docs"]

_shell: tuple[float, str] | None = None

# Features add their own addresses to the sitemap: SITEMAP_PROVIDERS.append(fn), where fn(db) returns [(path, last changed date or None)].
SITEMAP_PROVIDERS: list = []


def _site() -> str:
    return settings.site_url.rstrip("/")


def _get_shell() -> str | None:
    """The website's own index.html (so the page loads the same scripts), kept for a few minutes."""
    global _shell
    if _shell and time.monotonic() - _shell[0] < SHELL_SECONDS:
        return _shell[1]
    try:
        res = requests.get(f"{_site()}/index.html", timeout=3)
        res.raise_for_status()
        if "<!--seo-->" not in res.text or ROOT not in res.text:
            raise ValueError("the website's index.html has no seo block")
        _shell = (time.monotonic(), res.text)
        return res.text
    except (requests.RequestException, ValueError):
        log.warning("Couldn't read the website's index.html for game pages", exc_info=True)
        return _shell[1] if _shell else None  # an old copy beats none


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split("-"))


def slugify(name: str) -> str:
    """Words for an address: "Moon Garden!" -> "moon-garden". Same as the website's (src/GamePage.tsx)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")


def indie_path(game: IndieGame) -> str:
    """start.ng/creators/<id>-<name>: the id finds the game, the name is for people and search."""
    slug = slugify(game.name)
    return f"/creators/{game.id}-{slug}" if slug else f"/creators/{game.id}"


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _page(shell: str, *, title: str, description: str, path: str, image: str, body: str, noindex: bool = False, extra_head: str = "") -> str:
    url = f"{_site()}{path}"
    robots = "noindex, nofollow" if noindex else "index, follow, max-image-preview:large"
    head = "\n    ".join([
        "<!--seo-->",
        f"<title>{_esc(title)}</title>",
        f'<meta name="description" content="{_esc(description)}" />',
        f'<meta name="robots" content="{robots}" />',
        f'<link rel="canonical" href="{_esc(url)}" />',
        '<meta property="og:type" content="website" />',
        '<meta property="og:site_name" content="start.ng" />',
        f'<meta property="og:title" content="{_esc(title)}" />',
        f'<meta property="og:description" content="{_esc(description)}" />',
        f'<meta property="og:url" content="{_esc(url)}" />',
        f'<meta property="og:image" content="{_esc(image)}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{_esc(title)}" />',
        f'<meta name="twitter:description" content="{_esc(description)}" />',
        f'<meta name="twitter:image" content="{_esc(image)}" />',
        extra_head,
        "<!--/seo-->",
    ])
    out = SEO_BLOCK.sub(lambda _m: head, shell, count=1)
    return out.replace(ROOT, f'<div id="root">{body}</div>', 1)


@router.get("/games/{name}", response_class=Response)
def game_page(name: str = Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$"), db: Session = Depends(get_db)):
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")

    game = db.get(Game, name)
    if not game or game.hidden:  # hidden while admins look at it (app/admin.py)
        body = "<main><h1>Game not found</h1><p>There's no game with that name on start.ng.</p><p><a href=\"/games\">Browse all games</a></p></main>"
        page = _page(shell, title="Game not found · start.ng", description="There's no game with that name on start.ng. Browse the games instead.",
                     path=f"/games/{name}", image=_site() + SHARE_IMAGE, body=body, noindex=True)
        return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})

    title = _title(game.name)
    by = _player_name(game.owner, "a builder")
    description = game.description or f"Play {title} free in your browser on start.ng, a game by {by}."
    image = _cover_url(game) or _site() + SHARE_IMAGE
    path = f"/games/{game.name}"
    ld = {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": title,
        "description": description,
        "url": f"{_site()}{path}",
        "image": image,
        "author": {"@type": "Person", "name": by},
        "gamePlatform": "Web browser",
        "applicationCategory": "Game",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    extra = f'<script type="application/ld+json">{json.dumps(ld).replace("<", chr(92) + "u003c")}</script>'
    body = (
        f"<main><h1>{_esc(title)}</h1><p>{_esc(description)}</p><p>By {_esc(by)}</p>"
        f'<p><a href="{_esc(game_url(game.name))}">Play {_esc(title)}</a> · <a href="/games">All games</a></p></main>'
    )
    page = _page(shell, title=f"{title} · Play free on start.ng", description=description, path=path, image=image, body=body, extra_head=extra)
    return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


@router.get("/creators/{game_id}", response_class=Response)
def indie_page(game_id: int = Path(ge=1, le=2_000_000_000), db: Session = Depends(get_db)):
    """The page of a game by an independent creator (hosted outside start.ng), like game_page above."""
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")

    game = db.scalar(select(IndieGame).options(defer(IndieGame.cover), selectinload(IndieGame.owner)).where(IndieGame.id == game_id))
    if not game:
        body = "<main><h1>Game not found</h1><p>There's no game with that address on start.ng.</p><p><a href=\"/creators\">Browse games by independent creators</a></p></main>"
        page = _page(shell, title="Game not found · start.ng", description="There's no game with that address on start.ng. Browse the games instead.",
                     path=f"/creators/{game_id}", image=_site() + SHARE_IMAGE, body=body, noindex=True)
        return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})

    by = _player_name(game.owner, "an independent creator")
    description = game.description
    image = f"{settings.api_url.rstrip('/')}/indie/{game.id}/cover.webp"
    path = indie_path(game)
    ld = {
        "@context": "https://schema.org",
        "@type": "VideoGame",
        "name": game.name,
        "description": description,
        "url": f"{_site()}{path}",
        "image": image,
        "author": {"@type": "Person", "name": by},
        "gamePlatform": "Web browser",
        "applicationCategory": "Game",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    extra = f'<script type="application/ld+json">{json.dumps(ld).replace("<", chr(92) + "u003c")}</script>'
    body = (
        f"<main><h1>{_esc(game.name)}</h1><p>{_esc(description)}</p><p>By {_esc(by)}, an independent creator</p>"
        f'<p><a href="/creators">All games by independent creators</a></p></main>'
    )
    page = _page(shell, title=f"{game.name} · Play free on start.ng", description=description, path=path, image=image, body=body, extra_head=extra)
    return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


@router.get("/sitemap.xml", response_class=Response)
def sitemap(db: Session = Depends(get_db)):
    site = _site()
    urls = [f"  <url><loc>{_esc(site + p)}</loc></url>" for p in FIXED_PAGES]
    for name, updated in db.execute(select(Game.name, Game.updated_at).where(Game.hidden.is_(None)).order_by(Game.name)):
        when = (updated.replace(tzinfo=updated.tzinfo or timezone.utc)).date().isoformat() if updated else None
        lastmod = f"<lastmod>{when}</lastmod>" if when else ""
        urls.append(f"  <url><loc>{_esc(f'{site}/games/{name}')}</loc>{lastmod}</url>")
    for game in db.scalars(select(IndieGame).options(defer(IndieGame.cover), selectinload(IndieGame.owner)).order_by(IndieGame.id)):
        urls.append(f"  <url><loc>{_esc(site + indie_path(game))}</loc></url>")
    for provider in SITEMAP_PROVIDERS:
        for path, when in provider(db):
            urls.append(f"  <url><loc>{_esc(site + path)}</loc>{f'<lastmod>{when}</lastmod>' if when else ''}</url>")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>\n"
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})
