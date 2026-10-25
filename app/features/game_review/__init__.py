"""F2 game review: a new game waits for an admin before it's listed on start.ng; its owner is emailed the result."""
from . import service  # noqa: F401  (subscribes to game_published and game_updated, declares its email kinds)
from .router import router  # noqa: F401

NAME = "game_review"
PERMISSIONS: dict[str, str] = {
    "games.review": "Approve or reject new games before they're listed",
}
