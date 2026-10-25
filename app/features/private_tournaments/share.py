"""What a pasted invite link looks like in a chat: a page with the tournament's name in its preview tags, and a picture
(a start.ng card with the name, the games and how many are playing). The website's nginx asks for the page, see
startng-fe nginx.conf; the picture is the page's preview image. Both are for people who hold the link, so the page is
noindex and shows no more than the invite link already does (title, who made it, games, how many players)."""
import hashlib
import io
import logging
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ...config import settings
from ...routers import pages
from ...routers.games import _cover_url
from .models import PrivateTournament

log = logging.getLogger(__name__)

W, H = 1200, 630
LIME, INK, PAPER, MUTED = (216, 253, 113), (41, 42, 35), (246, 245, 238), (107, 108, 99)


COVER_SECONDS = 600  # a game's picture is kept this long before it is fetched again
_covers: dict[str, tuple[float, bytes | None]] = {}


def _title(name: str) -> str:
    return pages._title(name)


def _fetch(url: str) -> bytes | None:
    """A game's cover picture (None if it can't be had: the card then shows a coloured tile instead)."""
    hit = _covers.get(url)
    if hit and time.monotonic() - hit[0] < COVER_SECONDS:
        return hit[1]
    try:
        res = requests.get(url, timeout=2)
        res.raise_for_status()
        data = res.content if len(res.content) < 2_000_000 else None
    except requests.RequestException:
        log.warning("Couldn't fetch a cover for a tournament card: %s", url)
        data = None
    if len(_covers) > 200:
        _covers.clear()
    _covers[url] = (time.monotonic(), data)
    return data


def _tile(name: str, data: bytes | None, size: tuple[int, int]) -> Image.Image:
    """A game's cover cropped to the tile, or a coloured tile with its first letter."""
    if data:
        try:
            return ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), size)
        except Exception:
            log.warning("A cover for a tournament card isn't a picture: %s", name)
    h = hashlib.sha256(name.encode()).digest()
    tile = Image.new("RGB", size, (60 + h[0] % 150, 60 + h[1] % 150, 60 + h[2] % 150))
    d = ImageDraw.Draw(tile)
    d.text((size[0] // 2, size[1] // 2), name[:1].upper(), font=ImageFont.load_default(size=int(size[1] * .6)), fill=(255, 255, 255), anchor="mm")
    return tile


def card_png(t: PrivateTournament, members: int, owner: str) -> bytes:
    """The 1200x630 preview card."""
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 14), fill=LIME)
    d.text((70, 50), "start.ng", font=ImageFont.load_default(size=40), fill=INK)
    d.text((70, 112), "TOURNAMENT WITH FRIENDS", font=ImageFont.load_default(size=24), fill=MUTED)
    big = ImageFont.load_default(size=70)
    lines = textwrap.wrap(t.title, width=26)[:2] or [t.title]
    if len(lines) == 2 and len(t.title) > 52:
        lines[1] = lines[1][:23] + "…"
    y = 180
    for line in lines:
        d.text((70, y), line, font=big, fill=INK)
        y += 82
    # the picked games, side by side: their covers sum up what the tournament is about. F9: an admin's tournament keeps
    # its games for the people who register.
    games = [] if t.by_admin else [g.game_name for g in t.games][:5]
    gap, ty = 16, 360
    tw = min((W - 140 - gap * (len(games) - 1)) // max(1, len(games)), 220)
    th = int(tw * .62)
    if not games:
        d.text((70, ty + 40), "Register to see the games", font=ImageFont.load_default(size=40), fill=INK)
    urls = [_cover_url(g.game) for g in t.games[:len(games)]]  # read here: the database session isn't for other threads
    with ThreadPoolExecutor(max_workers=5) as pool:
        covers = list(pool.map(lambda url: _fetch(url) if url else None, urls))
    for i, (name, data) in enumerate(zip(games, covers)):
        x = 70 + i * (tw + gap)
        img.paste(_tile(_title(name), data, (tw, th)), (x, ty))
        d.rounded_rectangle((x, ty, x + tw, ty + th), radius=2, outline=INK, width=2)
        d.text((x, ty + th + 8), textwrap.shorten(_title(name), width=max(8, tw // 14), placeholder="…"), font=ImageFont.load_default(size=22), fill=INK)
    d.rounded_rectangle((70, 535, W - 70, 597), radius=16, fill=LIME)
    count = "" if t.by_admin else f"{len(t.games)} {'game' if len(t.games) == 1 else 'games'} · "
    sub = f"{count}{members} {'player' if members == 1 else 'players'} · by {owner}"
    d.text((96, 566), sub, font=ImageFont.load_default(size=30), fill=INK, anchor="lm")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def page_html(shell: str, t: PrivateTournament, members: int, owner: str) -> str:
    """The website's HTML with this tournament's title, description and card already in it."""
    games = ", ".join(_title(g.game_name) for g in t.games)
    title = f"{t.title} · tournament with friends · start.ng"
    description = f"{owner} invited you to \"{t.title}\" on start.ng: {len(t.games)} {'game' if len(t.games) == 1 else 'games'} ({games}), {members} {'player' if members == 1 else 'players'}. Join and see where you stand."
    if t.by_admin:  # F9: the games are for the people who register
        title = f"{t.title} · start.ng tournament"
        description = f"You're invited to \"{t.title}\" on start.ng. {members} {'player has' if members == 1 else 'players have'} registered. Register to see the games and play."
    path = f"/friends/{t.code}"
    image = f"{settings.api_url.rstrip('/')}/private-tournaments/{t.code}/card.png"
    body = f"<main><h1>{pages._esc(t.title)}</h1><p>{pages._esc(description)}</p></main>"
    return pages._page(shell, title=title, description=description, path=path, image=image, body=body, noindex=True)
