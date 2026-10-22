"""Draws a Card as a picture: 1200x630 (X, link previews) or 1080x1080 (WhatsApp, Instagram). Pictures are kept in memory
by what's on them (Card.fingerprint), so a card is drawn once and then served from the cache."""
import hashlib
import io
import logging
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .service import SIZES, Card

log = logging.getLogger(__name__)

LIME, INK, PAPER, MUTED = (216, 253, 113), (41, 42, 35), (246, 245, 238), (107, 108, 99)
MAX_CACHED = 300
COVER_SECONDS = 600

_pictures: dict[tuple[str, str, str], bytes] = {}  # (kind/item, size, fingerprint) -> PNG
_covers: dict[str, tuple[float, bytes | None]] = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _fetch(url: str) -> bytes | None:
    """A cover picture, or None when it can't be had (the card then shows a coloured tile)."""
    hit = _covers.get(url)
    if hit and time.monotonic() - hit[0] < COVER_SECONDS:
        return hit[1]
    try:
        res = requests.get(url, timeout=2)
        res.raise_for_status()
        data = res.content if len(res.content) < 2_000_000 else None
    except requests.RequestException:
        log.warning("Couldn't fetch a cover for a share card: %s", url)
        data = None
    if len(_covers) > 200:
        _covers.clear()
    _covers[url] = (time.monotonic(), data)
    return data


def _tile(seed: str, data: bytes | None, size: tuple[int, int]) -> Image.Image:
    if data:
        try:
            return ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), size)
        except Exception:
            log.warning("A share card cover isn't a picture: %s", seed)
    h = hashlib.sha256(seed.encode()).digest()
    return Image.new("RGB", size, (60 + h[0] % 150, 60 + h[1] % 150, 60 + h[2] % 150))


def _lines(text: str, width: int, count: int) -> list[str]:
    lines = textwrap.wrap(text, width=width)[:count] or [text[:width]]
    if len(textwrap.wrap(text, width=width)) > count:
        lines[-1] = lines[-1][: max(1, width - 1)].rstrip() + "…"
    return lines


def draw(card: Card, size: str) -> bytes:
    """The card as a PNG."""
    w, h = SIZES[size]
    square = size == "square"
    pad = 70 if not square else 80
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 14), fill=LIME)
    d.text((pad, 50), "start.ng", font=_font(40), fill=INK)
    d.text((pad, 112 if not square else 118), card.kicker.upper()[:40], font=_font(24), fill=MUTED)

    y = 175 if not square else 200
    title_font = _font(70 if not square else 84)
    for line in _lines(card.title, 26 if not square else 22, 2):
        d.text((pad, y), line, font=title_font, fill=INK)
        y += 82 if not square else 98
    if card.subtitle:
        for line in _lines(card.subtitle, 52 if not square else 40, 2):
            d.text((pad, y + 6), line, font=_font(32), fill=MUTED)
            y += 42

    covers = card.covers[:5]
    if covers:
        gap = 16
        cy = max(y + 24, 322 if not square else 520)
        tw = min(220 if not square else 190, (w - pad * 2 - gap * (len(covers) - 1)) // len(covers))
        th = int(tw * .58)
        with ThreadPoolExecutor(max_workers=5) as pool:
            fetched = list(pool.map(_fetch, covers))
        for i, (url, data) in enumerate(zip(covers, fetched)):
            x = pad + i * (tw + gap)
            img.paste(_tile(url, data, (tw, th)), (x, cy))
            d.rectangle((x, cy, x + tw, cy + th), outline=INK, width=2)

    numbers = card.numbers[:3]
    bar_h = 62 if not numbers else 118
    by = h - bar_h - 38
    d.rounded_rectangle((pad, by, w - pad, by + bar_h), radius=16, fill=LIME)
    if numbers:
        col = (w - pad * 2) // len(numbers)
        for i, (label, value) in enumerate(numbers):
            cx = pad + 26 + i * col
            d.text((cx, by + 20), label.upper()[:18], font=_font(20), fill=INK)
            d.text((cx, by + 48), value[:14], font=_font(44), fill=INK)
    else:
        d.text((pad + 26, by + bar_h // 2), "Play on start.ng", font=_font(30), fill=INK, anchor="lm")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def picture(key: str, card: Card, size: str) -> bytes:
    """The card's picture from the cache, drawn first if its content is new."""
    k = (key, size, card.fingerprint())
    hit = _pictures.get(k)
    if hit is None:
        hit = draw(card, size)
        if len(_pictures) >= MAX_CACHED:
            _pictures.pop(next(iter(_pictures)))
        _pictures[k] = hit
    return hit
