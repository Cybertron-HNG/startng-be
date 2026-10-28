"""Screenshots (Ziki #19): a game shows up to four pictures of itself on its page, from "screenshots" in its startng.json.
Other features call service.py (screenshots_of); see the top of that file."""
from . import server_page, service  # noqa: F401  (service registers the startng.json section, server_page the game page part)
from .router import router  # noqa: F401

NAME = "screenshots"  # the feature flag: its routes and page parts stay off until FEATURES includes it
PERMISSIONS: dict[str, str] = {}  # no admin permissions
