"""About and FAQ pages for games by independent creators (hosted outside start.ng), same flag as the rest of game_pages.

start.ng can't read these games' files, so the only facts are what the creator typed when adding the game (its name and a
description), who they are, and where it is hosted. The pages say those and nothing else: they don't claim a game is free,
or works on a phone, or anything the creator didn't say. They are written on request, so there is nothing to approve and
nothing stored: change the description and the pages change. The addresses are /creators/<id>-<name>/about and /faq."""
import json
from urllib.parse import urlsplit

from fastapi import Depends, Path, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, defer, selectinload

from ...config import settings
from ...db import get_db
from ...models import IndieGame
from ...routers.games import _player_name
from ...routers.pages import INDIE_PAGE_PARTS, SHARE_IMAGE, SITEMAP_PROVIDERS, _esc, _get_shell, _page, _site, indie_path
from ...server_html import chrome
from .router import _ld, router

INDIE_KINDS = ("about", "faq")
LABELS = {"about": "About {t}", "faq": "{t} FAQ"}
SHORT = {"about": "About", "faq": "FAQ"}
GAME_ID = Path(ge=1, le=2_000_000_000)
KIND = Path(pattern=r"^(about|faq)$")


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or url).removeprefix("www.")
    except ValueError:
        return url


def _paragraphs(text: str) -> str:
    parts = [p.strip() for p in text.replace("\r", "").split("\n\n") if p.strip()]
    return "".join(f"<p>{_esc(' '.join(p.split()))}</p>" for p in parts)


def _short(text: str, limit: int = 155) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def questions(game: IndieGame, by: str) -> list[tuple[str, str]]:
    """What a person might ask, answered only from what the creator gave us."""
    name, host = game.name, _host(game.url)
    return [
        (f"What is {name}?", " ".join(game.description.split())),
        (f"Who made {name}?", f"{name} was made by {by}, an independent creator."),
        (f"Where can I play {name}?", f"{name} runs on its creator's own site, {host}. Open it from its page on start.ng and it opens in a new tab."),
        (f"Is {name} made by start.ng?", f"No. {name} is made and hosted by an independent creator. start.ng lists it and links to it."),
    ]


def _enabled() -> bool:
    from ... import flags
    return flags.is_on("game_pages")


def _not_found(shell: str, game_id: int, kind: str) -> Response:
    body = chrome('<main class="container game-sub"><h1>Page not found</h1><p>There is no page like that on start.ng.</p><p><a href="/creators">Browse games by independent creators</a></p></main>')
    page = _page(shell, title="Page not found · start.ng", description="There is no page like that on start.ng. Browse the games instead.",
                 path=f"/creators/{game_id}/{kind}", image=_site() + SHARE_IMAGE, body=body, noindex=True)
    return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def _load(db: Session, game_id: int) -> IndieGame | None:
    return db.scalar(select(IndieGame).options(defer(IndieGame.cover), selectinload(IndieGame.owner)).where(IndieGame.id == game_id))


@router.get("/pages/creators/{game_id}/{kind}", response_class=Response, include_in_schema=False)
def indie_sub_page(game_id: int = GAME_ID, kind: str = KIND, db: Session = Depends(get_db)):
    shell = _get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    game = _load(db, game_id)
    if game is None:
        return _not_found(shell, game_id, kind)

    name, site = game.name, _site()
    by = _player_name(game.owner, "an independent creator")
    base = indie_path(game)
    path = f"{base}/{kind}"
    image = f"{settings.api_url.rstrip('/')}/indie/{game.id}/cover.webp"
    title = LABELS[kind].format(t=name)
    qa = questions(game, by)

    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Independent creators", "item": f"{site}/creators"},
        {"@type": "ListItem", "position": 2, "name": name, "item": f"{site}{base}"},
        {"@type": "ListItem", "position": 3, "name": SHORT[kind], "item": f"{site}{path}"},
    ]}
    extra = [_ld(crumbs)]
    if kind == "faq":
        description = f"Common questions about {name}, a game by {by}: what it is, who made it and where to play it."
        body = "".join(f"<h2>{_esc(q)}</h2><p>{_esc(a)}</p>" for q, a in qa)
        extra.append(_ld({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa]}))
    else:
        description = _short(game.description)
        body = (
            f"<h2>What is {_esc(name)}?</h2>{_paragraphs(game.description)}"
            f"<h2>Who made {_esc(name)}?</h2><p>{_esc(name)} was made by {_esc(by)}, an independent creator.</p>"
            f"<h2>Where do I play {_esc(name)}?</h2><p>{_esc(name)} is hosted by its creator at {_esc(_host(game.url))}, outside start.ng. "
            f"Use the Play button to open it in a new tab.</p>"
        )
        extra.append(_ld({"@context": "https://schema.org", "@type": "VideoGame", "name": name, "description": game.description, "url": f"{site}{path}",
                          "image": image, "author": {"@type": "Person", "name": by}, "gamePlatform": "Web browser", "applicationCategory": "Game"}))

    other = "faq" if kind == "about" else "about"
    inner = (
        '<main class="container game-sub">'
        f'<p class="crumbs"><a href="/creators">Independent creators</a> › <a href="{_esc(base)}">{_esc(name)}</a> › {_esc(SHORT[kind])}</p>'
        f'<p class="eyebrow">{_esc(name)}</p><h1>{_esc(title)}</h1>'
        f'<div class="game-sub-body">{body}</div>'
        f'<p class="game-sub-play"><a class="btn" href="{_esc(game.url)}" target="_blank" rel="noopener noreferrer">Play {_esc(name)}</a> <a class="btn ghost" href="{_esc(base)}">{_esc(name)} on start.ng</a></p>'
        f'<nav class="game-sub-links" aria-label="More about {_esc(name)}"><h2>More about {_esc(name)}</h2><ul>'
        f'<li><a href="{_esc(base)}/{other}">{_esc(LABELS[other].format(t=name))}</a></li>'
        f'<li><a href="{_esc(base)}">{_esc(name)} on start.ng</a></li><li><a href="/creators">More games by independent creators</a></li></ul></nav></main>'
    )
    html = _page(shell, title=f"{title} · start.ng", description=description, path=path, image=image, body=chrome(inner), extra_head="\n    ".join(extra))
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


class PagesOut(BaseModel):
    kind: str
    label: str
    path: str


@router.get("/indie/{game_id}/pages", response_model=list[PagesOut])
def indie_pages(game_id: int = GAME_ID, db: Session = Depends(get_db)):
    """The pages of a game by an independent creator, so its page can link to them."""
    game = _load(db, game_id)
    if game is None:
        return []
    base = indie_path(game)
    return [PagesOut(kind=k, label=LABELS[k].format(t=game.name), path=f"{base}/{k}") for k in INDIE_KINDS]


def sitemap_urls(db: Session) -> list[tuple[str, str | None]]:
    if not _enabled():
        return []  # the routes answer 404 while the flag is off, so the sitemap must not list them
    out: list[tuple[str, str | None]] = []
    for game in db.scalars(select(IndieGame).options(defer(IndieGame.cover), selectinload(IndieGame.owner)).order_by(IndieGame.id)):
        when = game.created_at.date().isoformat() if game.created_at else None
        out += [(f"{indie_path(game)}/{k}", when) for k in INDIE_KINDS]
    return out


SITEMAP_PROVIDERS.append(sitemap_urls)


def _page_part(db: Session, game: IndieGame) -> str:
    """"More about this game" on an independent game's server-written page: the same words and classes as src/features/game_pages/IndiePages.tsx."""
    if not _enabled():
        return ""
    items = "".join(f'<li><a href="{_esc(indie_path(game))}/{k}">{_esc(LABELS[k].format(t=game.name))}</a></li>' for k in INDIE_KINDS)
    return f'<section class="game-tags" aria-label="More about this game"><p class="eyebrow">More about this game</p><ul>{items}</ul></section>'


INDIE_PAGE_PARTS.append(_page_part)
