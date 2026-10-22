"""S1 player profiles and badges: a page for every player (name, character, level, streak, badges, best scores and ranks,
favorites, tournament record), public by default with a switch to make it private. Badges are awarded by rules registered
with the Progress Engine (D1). Never shows an email."""
from . import badges, service  # noqa: F401  (badges registers the award rules, service the privacy check)
from .router import router  # noqa: F401

NAME = "profiles"
PERMISSIONS: dict[str, str] = {}
