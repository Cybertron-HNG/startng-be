"""Remix this game (Ziki #83): a creator can let others start their own game from theirs ("remix": true in startng.json),
and a remix says where it came from ("remix_of": "<game>"). A game's page shows a "Remix it" button (GitHub's own fork
page: start.ng copies nothing), "Remixed from <game> by <creator>", and how many times it was remixed, with links.
Remixes are published like every other game, so they go through the same checks."""
from . import server_pages  # noqa: F401  (adds the remix lines to a game's server-written page)
from . import service  # noqa: F401  (registers the startng.json "remix" and "remix_of" sections)
from .router import router  # noqa: F401

NAME = "remix"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {}
