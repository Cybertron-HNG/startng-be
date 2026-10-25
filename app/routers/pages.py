"""Pages for search engines and link previews.

The website is a client-side app, so crawlers (and chat apps making a link preview) get an empty page.
For a game's page, start.ng/games/<name>, nginx on the website asks this router for the page instead:
it is the website's own HTML with that game's title, description and preview image already in it,
plus a plain-text copy of the page inside #root. The browser then loads the app as usual and replaces
that text. If this router isn't reachable, nginx falls back to the plain page.

The homepage works differently: nginx serves the website's own homepage file and asks this router only for
the text inside #root (home_page below), so the scripts it loads always match the website that is live.

Also the sitemap, which lists every game (the site's fixed pages and the games from the database).
"""
import html
import json
import logging
import re
import time
from datetime import timezone

import requests
from fastapi import APIRouter, Depends, Path, Query, Response
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


# The homepage's text. Everything here is also on the real page, in the same words: keep it in step with the
# home view in startng-fe (src/App.tsx, and the About and questions in src/home-copy.ts).
HANGOUT = "hangout"  # start.ng's own game has its own card, so it isn't in the community list
HOME_GAMES = 8  # cards the homepage shows before "See all" (HOME_GAMES in src/App.tsx)
HOME_GAMES_LISTED = 200  # games listed by name, A to Z (AZ_GAMES in src/App.tsx)
HOME_ABOUT = [
    "start.ng is Nigeria's home for browser games, built by the HNG community. Every game is free to play in your "
    "browser with no download, and many are made in Naija: danfo rush hour, jollof catching, Lagos markets and more.",
    "We're also building a home for web games from across Africa. If you make games anywhere on the continent, "
    "you can put yours on start.ng for free.",
]
HOME_QUESTIONS = [
    ("What is start.ng?",
     "A home for free browser games made by the HNG community. Each game has its own address, like "
     "yourgame.start.ng, and you play it right in your browser."),
    ("Is it free?", "Yes. Every game on start.ng is free to play in your browser, with nothing to download."),
    ("Are there Nigerian or African web games?",
     "Yes. Many games here are Naija-themed, from danfo rush hour to jollof and Lagos markets, and we welcome "
     "games from across Africa."),
    ("Do I need an account to play?",
     "No. Sign in to get your start.ng character, keep your scores on the leaderboards and join the daily tournament."),
    ("What is the daily tournament?",
     "Every day a few start.ng games are picked for 24 hours, and the best player wins. Points count towards the "
     "overall leaderboard."),
    ("How do I put my game on start.ng?",
     "Push your browser game to a public GitHub repo, publish it with one API call and approve it once. It goes "
     "live at yourgame.start.ng. The docs have every step."),
]
# (title, text) for "Got a game? Put it on start.ng"; the text is ours, not a player's, so it may hold <code>.
HOW_STEPS = [
    ("Build it", "Any static browser game: HTML, JavaScript, three.js, Phaser. Players bring their start.ng character, scores and friends."),
    ("Push it to GitHub", "A public repo with a ready-to-play <code>index.html</code> at the top or in <code>dist/</code>, plus a <code>cover.png</code> for its card."),
    ("Publish it", "One API call with your repo link, no key needed. Or hand your AI <code>start.ng/docs</code> and let it do the lot."),
    ("Approve it once", "Open the link you get back and it's live at <code>yourgame.start.ng</code>, with its own leaderboard if you want one. After that, every push updates it."),
]
# The site's header and footer, as the app draws them (src/App.tsx).
NAV = (
    '<header class="container nav"><a class="logo" href="/"><b>start<span>.ng</span></b></a>'
    '<nav aria-label="Pages"><a href="/games">Games</a><a href="/tournament">Tournament</a>'
    '<a href="/creators">Creators</a><a href="/docs">Docs</a></nav></header>'
)
FOOTER = '<footer class="container footer"><span>start<span>.ng</span></span><span>Built by the HNG community</span></footer>'


def _visible_games(db: Session) -> list[Game]:
    """The community's games, newest first, as GET /games lists them: not hidden, and not the Hangout."""
    return list(db.scalars(
        select(Game).options(selectinload(Game.owner))
        .where(Game.hidden.is_(None), Game.name != HANGOUT).order_by(Game.updated_at.desc())
    ))


def _home_body(games: list[Game]) -> str:
    listed = sorted(games, key=lambda g: _title(g.name).lower())[:HOME_GAMES_LISTED]
    total = len(games)
    links = "".join(
        f'<li><a href="/games/{_esc(g.name)}">{_esc(_title(g.name))}</a> <span>by {_esc(_player_name(g.owner, "A builder"))}</span></li>'
        for g in listed
    )
    az = f'<details class="az-games"><summary>Every game on start.ng, A to Z ({total})</summary><ul>{links}</ul></details>' if games else ""
    see_all = f'<div class="see-all"><a class="btn" href="/games">See all {total} games →</a></div>' if total > HOME_GAMES else ""
    steps = "".join(f"<li><b>{_esc(t)}</b><span>{text}</span></li>" for t, text in HOW_STEPS)
    about = "".join(f"<p>{_esc(p)}</p>" for p in HOME_ABOUT)
    questions = "".join(f"<details><summary>{_esc(q)}</summary><p>{_esc(a)}</p></details>" for q, a in HOME_QUESTIONS)
    return (
        f"{NAV}<main>"
        '<section class="container hero"><p class="eyebrow">Big imagination. Played everywhere.</p>'
        "<h1>Good vibes.<br><mark>Good games.</mark></h1>"
        '<p class="lead">Games made by builders in the community. Each one lives at its own start.ng address. '
        "Pick one and play right in your browser.</p>"
        '<div class="actions"><a class="btn" href="#games">Play now</a></div></section>'
        f'<section id="games" class="container"><div class="section-head"><div><h2>Games from the community</h2></div></div>{see_all}{az}</section>'
        f'<section id="how" class="container how"><h2>Got a game? Put it on start.ng</h2><ol>{steps}</ol>'
        '<div class="how-actions"><a class="btn" href="/docs">Read the docs</a>'
        '<a class="btn ghost" href="/docs.md">Docs for your AI (.md)</a></div></section>'
        f'<section id="about" class="container about"><h2>About start.ng</h2>{about}'
        f'<h3>Questions</h3><div class="faq">{questions}</div></section>'
        f"</main>{FOOTER}"
    )


@router.get("/home", response_class=Response)
def home_page(db: Session = Depends(get_db)):
    """The homepage's text as HTML for inside #root, not a whole page: nginx includes it in the homepage file it
    serves itself (an SSI include, see nginx.conf in startng-fe), so search engines and AI tools read the
    homepage, the games and the questions without running the app. The app replaces it when it loads."""
    return Response(_home_body(_visible_games(db)), media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


# /games: every game, PER_PAGE to a page (PER_PAGE in src/App.tsx), the Hangout first, then newest first.
PER_PAGE = 20
HANGOUT_CARD = ("The Hangout", "/hangout", "start.ng/hangout", "start.ng")  # HANGOUT in src/games.ts


def _pagination(page: int, pages: int) -> str:
    """The same page links as the app's (src/Pagination.tsx): first, last, and two either side of this page."""
    if pages <= 1:
        return ""
    def href(n: int) -> str:
        return "/games" if n == 1 else f"/games?page={n}"

    shown = sorted(n for n in {1, pages, page - 2, page - 1, page, page + 1, page + 2} if 1 <= n <= pages)
    current = ' aria-current="page"'
    numbers = "".join(
        ('<span class="gap">…</span>' if i and n - shown[i - 1] > 1 else "")
        + f'<a href="{href(n)}" aria-label="Page {n}"{current if n == page else ""}>{n}</a>'
        for i, n in enumerate(shown)
    )
    prev = f'<a href="{href(page - 1)}" rel="prev" aria-label="Previous page">← Previous</a>' if page > 1 else '<span class="off">← Previous</span>'
    nxt = f'<a href="{href(page + 1)}" rel="next" aria-label="Next page">Next →</a>' if page < pages else '<span class="off">Next →</span>'
    return f'<nav class="pagination" aria-label="Pages">{prev}<span class="numbers">{numbers}</span>{nxt}</nav>'


def _games_body(games: list[Game], page: int) -> str | None:
    """None for a page past the last one."""
    cards = [HANGOUT_CARD] + [
        (_title(g.name), f"/games/{g.name}", game_url(g.name).removeprefix("https://"), _player_name(g.owner, "A builder"))
        for g in games
    ]
    pages = max(1, -(-len(cards) // PER_PAGE))
    if page > pages:
        return None
    shown = cards[(page - 1) * PER_PAGE:page * PER_PAGE]
    start = (page - 1) * PER_PAGE + 1
    count = f"{start}–{start + len(shown) - 1} of {len(cards)}" if pages > 1 else f"{len(cards)} {'game' if len(cards) == 1 else 'games'}"
    empty = (
        '<p class="empty">No community games yet. The first ones are on their way. <a href="/#how">Put yours here.</a></p>'
        if not games else ""
    )
    items = "".join(
        f'<article class="card"><h3><a href="{_esc(link)}">{_esc(title)}</a></h3><p>{_esc(host)}</p><span class="by">by {_esc(by)}</span></article>'
        for title, link, host, by in shown
    )
    return (
        f"{NAV}<main>"
        '<section id="games" class="container all-games"><div class="section-head"><h1>All games from the community</h1></div>'
        f'<div class="list-tools"><span class="count">{count}</span></div>{empty}<div class="grid">{items}</div>'
        f"{_pagination(page, pages)}</section>"
        f"</main>{FOOTER}"
    )


@router.get("/games", response_class=Response)
def games_page(page: int = Query(1, ge=1, le=100_000), db: Session = Depends(get_db)):
    """/games' text as HTML for inside #root, one page of games at a time (?page=2), like home_page: nginx includes
    it in the games page it serves itself, so search engines can follow every page to every game. Searches and
    filters (?q=, ?category= ...) aren't rendered here: nginx keeps those addresses out of search results.
    A page past the last one is a 404, which nginx turns into the site's 404 page (the app shows "Page not found")."""
    body = _games_body(_visible_games(db), page)
    if body is None:
        return Response("No such page", status_code=404, media_type="text/plain", headers={"Cache-Control": "public, max-age=60"})
    return Response(body, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


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
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>\n"
    return Response(xml, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})
