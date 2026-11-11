"""Play here (Ziki #21): a game's page on start.ng can run the game in a frame on the page itself, so players keep the
leaderboard, categories and share buttons in view. The website does the playing (startng-fe src/features/play_here);
this feature is its flag, and the same "Play here" link on the game's server-written page."""
from . import server_page  # noqa: F401

NAME = "play_here"  # the feature flag: nothing shows until FEATURES includes it
router = None
PERMISSIONS: dict[str, str] = {}
