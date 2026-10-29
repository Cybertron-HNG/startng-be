"""Game Jams (Ziki #35): a theme and a deadline every month or so. Creators enter by publishing a game with "jam": "<slug>"
in startng.json while the jam is open, players who have played an entry vote for it during voting week, and the entries
with the most votes win. Admins make the jams. The page is /jam/<slug>, written by the server too (server_page.py)."""
from . import server_page, service  # noqa: F401  (registers the startng.json "jam" section and the page)
from .router import router  # noqa: F401

NAME = "jams"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    "jams.manage": "Create game jams and change their dates",
}
