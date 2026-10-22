"""B2 share cards: a link, ready text and a picture (1200x630 and 1080x1080) for something worth sharing, and the
/s/<kind>/<id> page whose preview tags make a pasted link show the card. Features supply what a card says through
share.register(kind, describe); see service.py (PRD C8)."""
from . import service, tournament_result  # noqa: F401  (tournament_result registers its kind)
from .router import router  # noqa: F401

NAME = "share"
PERMISSIONS: dict[str, str] = {}
