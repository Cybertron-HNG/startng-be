"""Share cards (B2). The contract other features use (PRD C8):

    from app.features.share import service as share

    share.register("challenge", describe)       # once, at import: describe(db, id) -> share.Card | None
    share.make(db, "challenge", code)           # -> share.Shared(url, text, image_url), or None

A feature says what a card shows (its title, a line under it, a few numbers, optional game covers, where the link
leads and the text to post); this feature draws it, caches the picture, and serves the /s/<kind>/<id> page whose
preview tags make a pasted link show the card. Kinds: challenge, tournament_result, unlock, league_move,
private_tournament, creator, profile (each feature registers its own).
"""
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Callable

from sqlalchemy.orm import Session

from ...config import settings

NAME = "share"

SIZES = {"wide": (1200, 630), "square": (1080, 1080)}  # X and link previews; WhatsApp and Instagram


@dataclass
class Card:
    kicker: str  # small capitals above the title: "DAILY TOURNAMENT"
    title: str
    subtitle: str = ""
    numbers: list[tuple[str, str]] = field(default_factory=list)  # up to 3: ("Score", "4,200")
    covers: list[str] = field(default_factory=list)  # game cover picture URLs, up to 5
    target: str = "/"  # where the link leads, on start.ng: "/tournament"
    text: str = ""  # ready to post; the link is added after it
    description: str = ""  # for the page's preview tags (default: subtitle)

    def fingerprint(self) -> str:
        """Changes when anything on the picture changes, so a cached picture is never stale."""
        data = asdict(self)
        return sha256(repr((data["kicker"], data["title"], data["subtitle"], data["numbers"], data["covers"])).encode()).hexdigest()[:16]


@dataclass
class Shared:
    url: str
    text: str
    image_url: str


Describe = Callable[[Session, str], Card | None]
KINDS: dict[str, Describe] = {}


def register(kind: str, describe: Describe) -> None:
    KINDS[kind] = describe


def describe(db: Session, kind: str, item: str) -> Card | None:
    fn = KINDS.get(kind)
    return fn(db, item) if fn else None


def link(kind: str, item: str) -> str:
    return f"{settings.site_url.rstrip('/')}/s/{kind}/{item}"


def image_url(kind: str, item: str, size: str = "wide") -> str:
    return f"{settings.api_url.rstrip('/')}/share/{kind}/{item}/card.png?size={size}"


def make(db: Session, kind: str, item: str) -> Shared | None:
    """The link, the text to post and the picture for something, or None when it doesn't exist."""
    card = describe(db, kind, item)
    if card is None:
        return None
    return Shared(url=link(kind, item), text=card.text or card.title, image_url=image_url(kind, item))
