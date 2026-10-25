"""Pages about each game (PRD workstream 2, Chloe): about, how to play, FAQ and walkthrough, written by the server so search
engines and people see the same page. Each is made from facts in the game's own files (startng.json, README, its page),
never invented, and a person approves it before it is public. A game that doesn't say enough gets no page and goes on a
"needs a description" list instead."""
from . import service  # noqa: F401
from .router import router  # noqa: F401
from . import indie  # noqa: F401  (About and FAQ pages for games by independent creators; adds its routes to the same router)

NAME = "game_pages"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    "gamepages.manage": "Make, read, edit and approve the about, how to play, FAQ and walkthrough pages of games",
}
