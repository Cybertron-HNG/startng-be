"""Drawing a card (the design from Claude Design). Wide (1200x630): the start.ng mark and eyebrow, the name in green, the
title big and black, the facts and the game under it, the badge ring on the right, and a lime band at the bottom with
the call to action and a "Play at start.ng" button. Square (1080x1080): the same parts stacked, the ring at the top.
Drawn cards are kept in memory by the card's version, so a card that hasn't changed is never drawn twice."""
import io
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .service import Badge, Card

SIZES = {"wide": (1200, 630), "square": (1080, 1080)}
FONT = Path(__file__).parent / "fonts" / "Archivo.ttf"  # Archivo, SIL Open Font License (fonts/OFL.txt)

LIME, INK, PAPER = (216, 253, 113), (30, 31, 26), (246, 245, 238)  # the website's colours
MUTED, GREEN = (92, 93, 86), (74, 94, 20)
TONES = {  # badge ring, then its value
    "gold": ((224, 176, 52), (122, 88, 0)),
    "silver": ((170, 176, 184), (72, 80, 92)),
    "bronze": ((200, 134, 77), (125, 74, 30)),
    "lime": (LIME, INK),
}
JPEG_QUALITY = 90  # small enough for WhatsApp's previews, which skip big pictures
CARDS_KEPT = 300  # drawn cards kept in memory (about 50 KB each)

_cards: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_lock = threading.Lock()  # fonts are shared, and FreeType isn't safe to use from two threads at once


@lru_cache(maxsize=64)
def _font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), size)
    font.set_variation_by_name(weight)
    return font


def _tracked_width(text: str, font: ImageFont.FreeTypeFont, track: float) -> float:
    return font.getlength(text) + track * max(len(text) - 1, 0)


def _text(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill, track: float = 0) -> float:
    """Text on the baseline xy, with letter spacing (track, in pixels; negative is tighter). Returns its width."""
    x, y = xy
    if not track:
        d.text((x, y), text, font=font, fill=fill, anchor="ls")
        return font.getlength(text)
    for i, ch in enumerate(text):
        d.text((x + font.getlength(text[:i]) + track * i, y), ch, font=font, fill=fill, anchor="ls")
    return _tracked_width(text, font, track)


def _fit(text: str, font: ImageFont.FreeTypeFont, width: float, track: float = 0) -> str:
    """The text, cut short with … so it fits in width pixels."""
    if _tracked_width(text, font, track) <= width:
        return text
    while text and _tracked_width(text + "…", font, track) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _sized(text: str, weight: str, largest: int, smallest: int, width: float, track_em: float = 0) -> ImageFont.FreeTypeFont:
    """The largest font from largest down to smallest that fits text in width."""
    size = largest
    while size > smallest and _tracked_width(text, _font(size, weight), track_em * size) > width:
        size -= 2
    return _font(size, weight)


def _dot(d: ImageDraw.ImageDraw, x: float, y: float, r: float = 3) -> None:
    d.ellipse((x - r, y - r, x + r, y + r), fill=MUTED)


def _logo(d: ImageDraw.ImageDraw, x: float, y: float, s: int) -> None:
    """The start.ng mark: a lime square inside a dark one."""
    d.rounded_rectangle((x, y, x + s, y + s), radius=s * .24, fill=INK)
    m = s * .3
    d.rounded_rectangle((x + m, y + m, x + s - m, y + s - m), radius=s * .1, fill=LIME)


def _controller(d: ImageDraw.ImageDraw, x: float, cy: float, h: int, color) -> float:
    """A game controller icon, h pixels tall, centred on cy. Returns its width."""
    w, line = h * 1.6, max(2, round(h / 8))
    d.rounded_rectangle((x, cy - h / 2, x + w, cy + h / 2), radius=h / 2, outline=color, width=line)
    px, arm = x + w * .3, h * .2
    d.line((px - arm, cy, px + arm, cy), fill=color, width=line)
    d.line((px, cy - arm, px, cy + arm), fill=color, width=line)
    r = h * .09
    for bx, by in ((x + w * .66, cy + h * .08), (x + w * .76, cy - h * .08)):
        d.ellipse((bx - r, by - r, bx + r, by + r), fill=color)
    return w


def _arrow(d: ImageDraw.ImageDraw, x: float, cy: float, s: int, color) -> None:
    line = max(2, round(s / 7))
    d.line((x, cy, x + s, cy), fill=color, width=line)
    d.line((x + s * .55, cy - s * .45, x + s, cy), fill=color, width=line)
    d.line((x + s * .55, cy + s * .45, x + s, cy), fill=color, width=line)


def _badge(d: ImageDraw.ImageDraw, cx: float, cy: float, r: int, badge: Badge) -> None:
    """The ring: the value big in the middle (with its suffix raised), the label spaced out under it."""
    ring, ink = TONES.get(badge.tone, TONES["lime"])
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ring, width=round(r * .17))
    value = _sized(badge.value, "Black", round(r * 1.12), round(r * .4), r * 1.15 if badge.suffix else r * 1.4)
    sup = _font(round(value.size * .36), "Bold")
    vw, sw = value.getlength(badge.value), sup.getlength(badge.suffix) if badge.suffix else 0
    x = cx - (vw + sw) / 2
    base = cy + value.size * .32
    d.text((x, base), badge.value, font=value, fill=ink, anchor="ls")
    if badge.suffix:
        d.text((x + vw + 2, base - value.size * .5), badge.suffix, font=sup, fill=ink, anchor="ls")
    if badge.label:
        label = _font(round(r * .15), "Bold")
        track = label.size * .28
        text = badge.label.upper()
        _text(d, (cx - _tracked_width(text, label, track) / 2, base + r * .38), text, label, MUTED, track)


def _facts(d: ImageDraw.ImageDraw, x: float, y: float, card: Card, size: int, width: float) -> None:
    """22 points · 1 game · (controller) Hangout, on the baseline y."""
    bold, plain = _font(size, "Bold"), _font(size, "Regular")
    gap, end, mid = size * 1.2, x + width, y - size * .32
    for i, (value, label) in enumerate(card.facts[:3]):
        if i:
            _dot(d, x + gap / 2, mid)
            x += gap
        d.text((x, y), value, font=bold, fill=INK, anchor="ls")
        x += bold.getlength(value + " ")
        d.text((x, y), label, font=plain, fill=MUTED, anchor="ls")
        x += plain.getlength(label)
    if card.game and end - x > size * 5:  # left out when there's no room for more than a letter or two
        if card.facts:
            _dot(d, x + gap / 2, mid)
            x += gap
        x += _controller(d, x, y - size * .34, round(size * .62), INK) + size * .4
        name = _font(size, "Medium")
        d.text((x, y), _fit(card.game, name, end - x), font=name, fill=INK, anchor="ls")


def _band(d: ImageDraw.ImageDraw, W: int, H: int, top: int, margin: int, card: Card, scale: float) -> None:
    """The lime band: the call to action on the left, the dark button on the right."""
    d.rectangle((0, top, W, H), fill=LIME)
    cy = (top + H) / 2
    button = _font(round(30 * scale), "ExtraBold")
    bh, pad, arrow = round(60 * scale), round(28 * scale), round(20 * scale)
    bw = button.getlength(card.action) + pad * 2 + arrow + round(14 * scale)
    bx = W - margin - bw
    d.rounded_rectangle((bx, cy - bh / 2, W - margin, cy + bh / 2), radius=bh / 2, fill=INK)
    d.text((bx + pad, cy), card.action, font=button, fill=LIME, anchor="lm")
    _arrow(d, W - margin - pad - arrow, cy, arrow, LIME)
    if card.cta:
        room = bx - margin - 30 * scale
        cta = _sized(card.cta, "ExtraBold", round(36 * scale), round(24 * scale), room, -.02)
        track = -.02 * cta.size
        _text(d, (margin, cy + cta.size * .35), _fit(card.cta, cta, room, track), cta, INK, track)


def _header(d: ImageDraw.ImageDraw, x: float, y: float, card: Card, right: float, size: int) -> None:
    """The start.ng mark and name, then the eyebrow, on the baseline y; nothing goes past right."""
    _logo(d, x, y - size * .9, size)
    x += size * 1.5
    brand = _font(size, "ExtraBold")
    d.text((x, y), "start.ng", font=brand, fill=INK, anchor="ls")
    x += brand.getlength("start.ng")
    if card.eyebrow:
        _dot(d, x + size * .6, y - size * .32, 2.5)
        x += size * 1.2
        eyebrow = _font(size, "Medium")
        d.text((x, y), _fit(card.eyebrow, eyebrow, right - x), font=eyebrow, fill=MUTED, anchor="ls")


def draw(card: Card, size: str) -> bytes:
    """The card as a JPEG, size "wide" (1200x630) or "square" (1080x1080)."""
    key = (card.version(), size)
    with _lock:
        if key in _cards:
            _cards.move_to_end(key)
            return _cards[key]
        out = _draw_square(card) if size == "square" else _draw_wide(card)
        _cards[key] = out
        if len(_cards) > CARDS_KEPT:
            _cards.popitem(last=False)
    return out


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _draw_wide(card: Card) -> bytes:
    W, H, M, band = 1200, 630, 80, 526
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 13), fill=LIME)
    col = (760 if card.badge else W - M) - M  # the text column, left of the ring
    has_facts = bool(card.facts or card.game)

    title = _sized(card.title, "Black", 112, 60, col, -.04)
    name = _font(52, "SemiBold")
    # the stack's height, so it sits in the middle between the strip and the band
    height = 26 + 40 + (name.size + 6 if card.name else 0) + title.size * .86 + (76 if has_facts else 0)
    y = (13 + band) / 2 - height / 2 + 26
    _header(d, M, y, card, M + col, 26)
    y += 40
    if card.name:
        y += name.size
        d.text((M, y), _fit(card.name, name, col), font=name, fill=GREEN, anchor="ls")
        y += 6
    y += title.size * .86
    track = -.04 * title.size
    _text(d, (M - 3, y), _fit(card.title, title, col, track), title, INK, track)
    if has_facts:
        _facts(d, M, y + 76, card, 30, col)
    if card.badge:
        _badge(d, 970, (13 + band) / 2, 150, card.badge)
    _band(d, W, H, band, M, card, 1)
    return _jpeg(img)


def _draw_square(card: Card) -> bytes:
    W = H = 1080
    M, band = 80, 900
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 14), fill=LIME)
    col = W - 2 * M
    has_facts = bool(card.facts or card.game)

    _header(d, M, 110, card, W - M, 30)
    if card.badge:
        _badge(d, W - M - 160, 330, 160, card.badge)
    # from the bottom up: the facts, the title, the name
    title = _sized(card.title, "Black", 140, 64, col, -.04)
    y = band - 70 - (74 if has_facts else 0)
    if has_facts:
        _facts(d, M, y + 74, card, 34, col)
    track = -.04 * title.size
    _text(d, (M - 4, y), _fit(card.title, title, col, track), title, INK, track)
    if card.name:
        name = _font(64, "SemiBold")
        name_y = y - title.size * .86 - 24
        room = col - (360 if card.badge and name_y < 330 + 160 + name.size else 0)  # beside the ring, not under it
        d.text((M, name_y), _fit(card.name, name, room), font=name, fill=GREEN, anchor="ls")
    _band(d, W, H, band, M, card, 1.15)
    return _jpeg(img)
