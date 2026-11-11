"""The jam page as the server writes it (/jam/<slug>, nginx asks /pages/jam/<slug>): the same words, links and order as
startng-fe src/features/jams/JamPage.tsx, so search engines read what people see. Also lists the jams in the sitemap."""
from datetime import datetime, timezone

from fastapi import Depends, Response
from sqlalchemy.orm import Session

from ... import flags
from ...db import get_db
from ...routers import pages
from ...routers.games import _player_name
from ...routers.pages import _esc, _title
from ...server_html import chrome
from . import service
from .router import JAM, router
from .models import Jam


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def day(dt: datetime) -> str:
    """29 Oct 2026, in UTC: the app writes dates the same way (day() in JamPage.tsx)."""
    dt = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def when(jam: Jam) -> str:
    p = service.phase(jam)
    if p == "soon":
        return f"Opens {day(jam.opens_at)}. Entries close {day(jam.entries_close_at)}."
    if p == "open":
        return f"Taking entries until {day(jam.entries_close_at)}. Voting until {day(jam.voting_ends_at)}."
    if p == "voting":
        return f"Entries are closed. Voting until {day(jam.voting_ends_at)}: players who have played an entry can vote for it."
    return f"Finished on {day(jam.voting_ends_at)}."


def how_to_enter(jam: Jam) -> str | None:
    if service.phase(jam) not in ("soon", "open"):
        return None
    return f'To enter, publish your game with "jam": "{jam.slug}" in its startng.json before {day(jam.entries_close_at)}.'


def entry_line(e: service.Entry, over: bool) -> str:
    """One entry as the list shows it: "Coin Rush by Ada · Winner #1 · 12 votes"."""
    parts = [f"by {_player_name(e.game.owner, 'A builder')}"]
    if e.winner:
        parts.append(f"Winner #{e.winner}")
    if over:
        parts.append(f"{e.votes} vote" + ("" if e.votes == 1 else "s"))
    return " · ".join(parts)


@router.get("/pages/jam/{slug}", response_class=Response, include_in_schema=False)
def jam_server_page(slug: str = JAM, db: Session = Depends(get_db)):
    shell = pages._get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    jam = service.by_slug(db, slug)
    path = f"/jam/{slug}"
    if jam is None:
        body = chrome('<main class="container jam-page"><h1>No jam here</h1><p>There is no game jam at this address.</p><p><a href="/games">Browse all games</a></p></main>')
        page = pages._page(shell, title="No jam here · start.ng", description="There is no game jam at this address.", path=path,
                           image=pages._site() + pages.SHARE_IMAGE, body=body, noindex=True)
        return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
    over = service.phase(jam) == "over"
    items = service.entries(db, jam)
    listing = "".join(
        f'<li><a href="/games/{_esc(e.game.name)}">{_esc(_title(e.game.name))}</a> <span>{_esc(entry_line(e, over))}</span></li>' for e in items
    ) if items else ""
    enter = how_to_enter(jam)
    body = chrome(
        f'<main class="container jam-page"><p class="eyebrow">Game jam</p><h1>{_esc(jam.title)}</h1>'
        f'<p class="lead-sm">{_esc(jam.brief)}</p><p class="jam-when">{_esc(when(jam))}</p>'
        + (f'<p class="jam-enter">{_esc(enter)}</p>' if enter else "")
        + f"<h2>Entries ({len(items)})</h2>"
        + (f'<ul class="jam-entries">{listing}</ul>' if items else '<p class="empty">No entries yet.</p>')
        + "</main>"
    )
    description = f"{jam.title}: a game jam on start.ng. {jam.brief}"[:300]
    page = pages._page(shell, title=f"{jam.title} · Game jam · start.ng", description=description, path=path,
                       image=pages._site() + pages.SHARE_IMAGE, body=body)
    return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})


def sitemap_urls(db: Session) -> list:
    if not flags.is_on("jams"):
        return []  # the pages answer 404 while the flag is off, so the sitemap must not list them
    return [(f"/jam/{j.slug}", None) for j in service.all_jams(db) if service.phase(j) != "soon"]


pages.SITEMAP_PROVIDERS.append(sitemap_urls)
