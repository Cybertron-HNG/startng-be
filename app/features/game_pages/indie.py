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
from ...server_html import chrome, colors_for, esc
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


def _added(game: IndieGame) -> str | None:
    return game.created_at.strftime("%B %Y") if game.created_at else None


def questions(game: IndieGame, by: str) -> list[tuple[str, str]]:
    """What a person might ask, answered only from what the creator gave us and what start.ng itself knows."""
    name, host, added = game.name, _host(game.url), _added(game)
    qa = [
        (f"What is {name}?", " ".join(game.description.split())),
        (f"Who made {name}?", f"{name} was made by {by}, an independent creator."),
        (f"Where can I play {name}?", f"{name} runs on its creator's own site, {host}. Open it from its page on start.ng and it opens in a new tab."),
        (f"Is {name} made by start.ng?", f"No. {name} is made and hosted by an independent creator. start.ng lists it and links to it."),
    ]
    if added:
        qa.append((f"When was {name} added to start.ng?", f"{name} was added to start.ng in {added}."))
    qa.append((f"Does {name} have a leaderboard on start.ng?", f"No. {name} is hosted by its creator, outside start.ng, so start.ng does not keep its scores."))
    return qa


def _card(g: IndieGame) -> str:
    """A card that looks like the website's game cards (the same as server_html.game_card, for an independent game)."""
    a, b = colors_for(g.name)
    path = indie_path(g)
    cover = f"{settings.api_url.rstrip('/')}/indie/{g.id}/cover.webp"
    return (
        f'<article class="game"><a class="game-link" href="{esc(path)}">'
        f'<div class="art" style="background: linear-gradient(135deg, {a}, {b})"><img src="{esc(cover)}" alt="" loading="lazy" decoding="async"></div>'
        f'<div class="info"><h3>{esc(g.name)}</h3><p>{esc(_host(g.url))}</p><span class="by">by {esc(_player_name(g.owner, "an independent creator"))}</span></div></a>'
        f'<div class="meta"><a class="play" href="{esc(path)}">View</a></div></article>'
    )


def _more_games(db: Session, game: IndieGame, by: str) -> str:
    """More by the same creator, then the newest by other creators: links to other pages on start.ng, read live."""
    base = select(IndieGame).options(defer(IndieGame.cover), selectinload(IndieGame.owner)).where(IndieGame.id != game.id)
    mine = db.scalars(base.where(IndieGame.owner_id == game.owner_id).order_by(IndieGame.created_at.desc(), IndieGame.id.desc()).limit(4)).all()
    seen = {g.id for g in mine}
    others = [g for g in db.scalars(base.where(IndieGame.owner_id != game.owner_id).order_by(IndieGame.created_at.desc(), IndieGame.id.desc()).limit(8)).all() if g.id not in seen][:4]
    out = ""
    if mine:
        out += f'<section class="game-sub-more" aria-label="More games by {esc(by)}"><div class="section-head"><h2>More games by {esc(by)}</h2></div><div class="grid">{"".join(_card(g) for g in mine)}</div></section>'
    if others:
        out += f'<section class="game-sub-more" aria-label="More games by independent creators"><div class="section-head"><h2>More games by independent creators</h2></div><div class="grid">{"".join(_card(g) for g in others)}</div></section>'
    return out


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

    kind = next(k for k in INDIE_KINDS if k == kind)  # our own word for it, not the request's text (KIND only lets these through)
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
        added = _added(game)
        glance = [f"Made by {by}, an independent creator", f"Hosted by its creator at {_host(game.url)}, outside start.ng"]
        if added:
            glance.insert(1, f"On start.ng since {added}")
        glance.append("Opens in a new tab from its page on start.ng")
        body = (
            f"<h2>What is {_esc(name)}?</h2>{_paragraphs(game.description)}"
            f"<h2>{_esc(name)} at a glance</h2><ul>{''.join(f'<li>{_esc(x)}</li>' for x in glance)}</ul>"
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
        f'<li><a href="{_esc(base)}">{_esc(name)} on start.ng</a></li><li><a href="/creators">More games by independent creators</a></li></ul></nav>'
        f"{_more_games(db, game, by)}</main>"
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
