"""What a pasted invite link looks like in a chat: a page with the tournament's name in its preview tags, and a picture
(a start.ng card with the name, the games and how many are playing). The website's nginx asks for the page, see
startng-fe nginx.conf; the picture is the page's preview image. Both are for people who hold the link, so the page is
noindex and shows no more than the invite link already does (title, who made it, games, how many players)."""
import io
import json
import textwrap

from PIL import Image, ImageDraw, ImageFont

from ...config import settings
from ...routers import pages
from .models import PrivateTournament

W, H = 1200, 630
LIME, INK, PAPER, MUTED = (216, 253, 113), (41, 42, 35), (246, 245, 238), (107, 108, 99)


def _title(name: str) -> str:
    return pages._title(name)


def card_png(t: PrivateTournament, members: int, owner: str) -> bytes:
    """The 1200x630 preview card."""
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 14), fill=LIME)
    d.text((70, 60), "start.ng", font=ImageFont.load_default(size=44), fill=INK)
    d.text((70, 128), "TOURNAMENT WITH FRIENDS", font=ImageFont.load_default(size=26), fill=MUTED)
    big = ImageFont.load_default(size=88)
    lines = textwrap.wrap(t.title, width=22)[:2] or [t.title]
    if len(lines) == 2 and len(t.title) > 44:
        lines[1] = lines[1][:19] + "…"
    y = 190
    for line in lines:
        d.text((70, y), line, font=big, fill=INK)
        y += 104
    names = ", ".join(_title(g.game_name) for g in t.games)
    d.rounded_rectangle((70, 470, W - 70, 560), radius=20, fill=LIME)
    sub = f"{len(t.games)} {'game' if len(t.games) == 1 else 'games'} · {members} {'player' if members == 1 else 'players'} · by {owner}"
    d.text((100, 478), sub, font=ImageFont.load_default(size=34), fill=INK)
    d.text((100, 520), textwrap.shorten(names, width=70, placeholder="…"), font=ImageFont.load_default(size=26), fill=INK)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def page_html(shell: str, t: PrivateTournament, members: int, owner: str) -> str:
    """The website's HTML with this tournament's title, description and card already in it."""
    games = ", ".join(_title(g.game_name) for g in t.games)
    title = f"{t.title} · tournament with friends · start.ng"
    description = f"{owner} invited you to \"{t.title}\" on start.ng: {len(t.games)} {'game' if len(t.games) == 1 else 'games'} ({games}), {members} {'player' if members == 1 else 'players'}. Join and see where you stand."
    path = f"/friends/{t.code}"
    image = f"{settings.api_url.rstrip('/')}/private-tournaments/{t.code}/card.png"
    body = f"<main><h1>{pages._esc(t.title)}</h1><p>{pages._esc(description)}</p></main>"
    return pages._page(shell, title=title, description=description, path=path, image=image, body=body, noindex=True)
