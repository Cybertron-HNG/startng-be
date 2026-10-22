"""Share cards (PRD B2): one way for every feature to share something. share.make(kind, id) gives a link, ready text and a
card picture (1200x630 for X and link previews, 1080x1080 for WhatsApp). Each feature describes its own kind
(service.register); this feature only draws the card and serves start.ng/s/<kind>/<id>."""
from . import kinds  # noqa: F401  (registers the kinds start.ng's own code has no feature folder for)
from .router import router  # noqa: F401

NAME = "share_cards"  # the feature flag
PERMISSIONS: dict[str, str] = {}
