"""The share cards contract (PRD C8). A feature that wants something shared describes it once:

    from app.features.share_cards import service as share

    def describe_challenge(db: Session, id: str) -> share.Card | None:
        c = ...  # None when it doesn't exist or isn't public: the link then answers 404
        return share.Card(eyebrow="Challenge", name="Loki", title="scored 4,200",
                          facts=[("4,200", "score")], game="Relic Hunter",
                          badge=share.Badge("4.2", "k", "score"), cta="Can you beat 4,200?",
                          path=f"/c/{c.code}", text="I scored 4,200 on Relic Hunter. Can you beat it?")

    share.register("challenge", describe_challenge)       # once, when the feature is imported

and asks for the link when a player shares:

    s = share.make(db, "challenge", c.code)  # {url, text, image_url, square_image_url}, or None

In your feature's tests, call your describe function directly, or service.describe(db, kind, id): neither needs the
share_cards flag. service.make needs it on (monkeypatch settings.features to "*"). To see your card while you build it,
run the website with `npm run dev` and open /share-preview?kind=<kind>&id=<id>.

The url is start.ng/s/<kind>/<id>: a pasted link shows the card, and opening it takes people to card.path. Cards are
drawn from what describe returns each time, so they're never out of date; the picture's address changes with its
content (?v=), so chat apps don't keep showing an old one.
"""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from ... import flags
from ...config import settings

# Every kind of card, and who describes it (PRD B2). A kind that isn't here can't be registered, so a typo fails loudly.
KINDS: dict[str, str] = {
    "challenge": "B1 challenge links",
    "tournament_result": "the daily tournament (described in share_cards/kinds.py)",
    "score": "a single run score card on a game (described in share_cards/kinds.py)",
    "unlock": "K1 wardrobe rewards",
    "league_move": "M2 leagues",
    "private_tournament": "Tr1 private tournaments",
    "creator": "T2 creator hub",
    "profile": "S1 player profiles",
}

FEATURE = "share_cards"  # its flag, the same as NAME in __init__.py


@dataclass(frozen=True)
class Badge:
    """The ring on the right of the card: a big value ("3" with "rd"), a label under it ("place") and its colour."""
    value: str
    suffix: str = ""  # small, raised after the value
    label: str = ""
    tone: str = "lime"  # "gold", "silver", "bronze" (1st to 3rd) or "lime"


@dataclass(frozen=True)
class Card:
    title: str  # the big line: "placed 3rd"
    path: str  # where the link takes people on start.ng: "/tournament"
    text: str  # ready-made words for the post (the link is added by whoever shares)
    name: str = ""  # who, in green above the title: "Ada"
    eyebrow: str = ""  # next to the start.ng name at the top: "Daily tournament · Wed 21 Oct"
    facts: list[tuple[str, str]] = field(default_factory=list)  # up to 3 under the title: [("22", "points"), ("1", "game")]
    game: str = ""  # a game's name, after the facts with a controller icon
    badge: Badge | None = None
    cta: str = ""  # the line in the lime band at the bottom: "Think you can beat 22 points?"
    action: str = "Play at start.ng"  # the button next to it

    @property
    def headline(self) -> str:
        """The name and title as one line, for page titles and alt text: "Ada placed 3rd"."""
        return f"{self.name} {self.title}".strip()

    def version(self) -> str:
        """Changes whenever anything on the card does: the picture's ?v=, and the key of its cached image."""
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Share:
    url: str
    text: str
    image_url: str  # 1200x630: X, Facebook, link previews
    square_image_url: str  # 1080x1080: WhatsApp, Instagram


Describe = Callable[[Session, str], Card | None]
_describers: dict[str, Describe] = {}


def register(kind: str, describe: Describe) -> None:
    """Describe a kind of card. describe(db, id) returns a Card, or None when there's nothing to share at that id
    (missing, deleted, private): its link then answers 404."""
    if kind not in KINDS:
        raise KeyError(f'Unknown share kind "{kind}". Add it to KINDS in app/features/share_cards/service.py first.')
    if _describers.get(kind) not in (None, describe):
        raise ValueError(f'Share kind "{kind}" is already described by {_describers[kind].__module__}')
    _describers[kind] = describe


def describe(db: Session, kind: str, id: str) -> Card | None:
    fn = _describers.get(kind)
    return fn(db, id) if fn else None


def links(kind: str, id: str, card: Card) -> Share:
    api = settings.api_url.rstrip("/")
    image = f"{api}/share/{kind}/{id}/card.jpg?v={card.version()}"
    return Share(url=f"{settings.site_url.rstrip('/')}/s/{kind}/{id}", text=card.text,
                 image_url=image, square_image_url=f"{image}&size=square")


def make(db: Session, kind: str, id: str) -> Share | None:
    """The link, words and pictures for sharing one thing. None while share cards are off or there's nothing to share,
    so the caller can hide its Share button."""
    if not flags.is_on(FEATURE):
        return None
    card = describe(db, kind, str(id))
    return links(kind, str(id), card) if card else None
