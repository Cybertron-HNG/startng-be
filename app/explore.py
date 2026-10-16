"""Explore games: games other people built and host on their own sites, listed on start.ng.

start.ng does not host these. It keeps a card (name, creator, link, description, cover picture)
and sends players to the creator's site, in a new tab. Anyone can submit one without signing in,
so every rule is checked here, on the server, whatever the website already checked:

- the link must be a public http or https address (nothing that points at a private network,
  carries a password, or is a start.ng game, which are published with the API instead)
- text is cleaned of control and look-alike characters
- the picture is opened, checked and drawn again as a fresh card-sized WebP (app/covers.py), so
  nothing hidden in an uploaded file is ever stored
"""
import base64
import binascii
import hashlib
import hmac
import io
import ipaddress
import re
import secrets
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError

from . import covers
from .checks import Problem
from .config import settings

NAME_LENGTH = (2, 40)
CREATOR_LENGTH = (2, 40)
DESCRIPTION_LENGTH = (20, 500)
URL_MAX = 300
MIN_COVER = (640, 400)


@dataclass
class FieldProblem(Problem):
    """A problem with one field of the form, so the website can show it next to that field."""

    field: str | None = None


# ---- text

# Control characters, and characters that reorder or hide text (used to make a link or a name
# read differently from what it is).
_HIDDEN = re.compile("[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def clean_line(value: str) -> str:
    """One line of text: no hidden characters, single spaces, no ends."""
    return re.sub(r"\s+", " ", _HIDDEN.sub("", unicodedata.normalize("NFC", value))).strip()


def clean_text(value: str) -> str:
    """A few sentences: line breaks are kept (at most one blank line in a row)."""
    text = _HIDDEN.sub("", unicodedata.normalize("NFC", value)).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def check_name(value: str, what: str, limits: tuple[int, int]) -> tuple[str, str | None]:
    text = clean_line(value)
    if not text:
        return text, f"Enter {what}."
    if len(text) < limits[0]:
        return text, f"This needs at least {limits[0]} characters."
    if len(text) > limits[1]:
        return text, f"Keep this to {limits[1]} characters or fewer."
    return text, None


def check_description(value: str) -> tuple[str, str | None]:
    text = clean_text(value)
    if not text:
        return text, "Describe your game in a few sentences."
    if len(text) < DESCRIPTION_LENGTH[0]:
        return text, f"Add a little more detail: {DESCRIPTION_LENGTH[0] - len(text)} more characters needed."
    if len(text) > DESCRIPTION_LENGTH[1]:
        return text, f"Keep the description to {DESCRIPTION_LENGTH[1]} characters or fewer."
    return text, None


# ---- links

# A real domain name: labels of letters, digits and hyphens, ending in a letters-only top level.
# (A numeric last part would let a browser read "0x7f.1" as 127.0.0.1.)
_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")


@dataclass
class CheckedUrl:
    url: str  # tidied: lowercase host, no fragment
    host: str


def _is_start_ng(host: str) -> bool:
    base = (settings.games_base_domain or "start.ng").lower()
    return host == base or host.endswith("." + base)


def check_url(raw: str) -> tuple[CheckedUrl | None, str | None]:
    """The tidied link, or what is wrong with it."""
    value = raw.strip()
    if not value:
        return None, "Enter the address where people play your game."
    if len(value) > URL_MAX:
        return None, f"That address is too long. Keep it under {URL_MAX} characters."
    if _HIDDEN.search(value) or re.search(r"\s", value):
        return None, "The address cannot contain spaces."
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None, "That does not look like a web address. Start it with https://, for example https://yourgame.com"
    if not parts.scheme:
        return None, "Start the address with https://, for example https://yourgame.com"
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None, f'Only http:// and https:// links are allowed. A "{scheme}:" link is not.'
    if not parts.hostname:
        return None, "That does not look like a web address. Start it with https://, for example https://yourgame.com"
    if parts.username is not None or parts.password is not None:
        return None, "Remove the username and password from the address."
    try:
        host = parts.hostname.lower().rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return None, "That address has characters a web address cannot have."
    if _is_start_ng(host):
        return None, "Games on start.ng are published with the API and appear on the Games page, not here. See the docs."
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            return None, "Use a public address. localhost and private network addresses cannot be opened by players."
    elif host == "localhost" or host.endswith(_PRIVATE_SUFFIXES) or "." not in host:
        return None, "Use a public address. localhost and private network addresses cannot be opened by players."
    elif not _HOST.match(host):
        return None, "That does not look like a web address. Check the spelling."
    netloc = f"[{host}]" if ip is not None and ip.version == 6 else host
    if port is not None:
        netloc += f":{port}"
    return CheckedUrl(urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")), host), None


# ---- the cover picture

def check_cover(value: str) -> tuple[bytes | None, str | None]:
    """A card-sized WebP from the picture the website sent (base64, optionally a data: link)."""
    raw = value.strip()
    if not raw:
        return None, "Add a cover image so your game has a card."
    if raw.startswith("data:"):
        header, _, raw = raw.partition(",")
        if ";base64" not in header.lower():
            return None, "That cover image could not be read. Choose the file again."
    if len(raw) * 3 // 4 > covers.MAX_UPLOAD_BYTES + 3:
        return None, "That image is over 2 MB. Use a smaller one."
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None, "That cover image could not be read. Choose the file again."
    if not data:
        return None, "That file is empty. Choose a PNG, JPEG or WebP image."
    if len(data) > covers.MAX_UPLOAD_BYTES:
        return None, "That image is over 2 MB. Use a smaller one."
    try:  # the real type comes from the file's own bytes, never from a name or a label
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"]) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None, "That file is not a PNG, JPEG or WebP image. SVG, GIF and other formats are not allowed."
    if width * height > covers.MAX_PIXELS:
        return None, "That picture is too big. Use one under 8000 x 5000."
    if width < MIN_COVER[0] or height < MIN_COVER[1]:
        return None, f"The image is {width} x {height}. Use at least {MIN_COVER[0]} x {MIN_COVER[1]}."
    try:
        return covers.make_cover(data), None
    except covers.CoverError as err:
        return None, str(err)


# ---- search

def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def matches(name: str, creator: str, query: str) -> bool:
    """Every word typed appears in the game's name or the creator's name (ignoring case and accents)."""
    terms = _fold(query).split()
    return all(t in _fold(name) or t in _fold(creator) for t in terms)


# ---- keys: how a creator removes their own listing without an account, and how a moderator can

def new_manage_key() -> str:
    return secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_matches(key: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_key(key), hashed)


def is_admin(token: str | None) -> bool:
    """True for the moderator token from the settings. Empty setting = nobody is a moderator."""
    return bool(settings.explore_admin_token) and bool(token) and hmac.compare_digest(
        token.encode(), settings.explore_admin_token.encode()
    )
