"""T1 categories, ratings and filters: a fixed list of categories (set in startng.json or by the owner), 1-5 star ratings
from players who played the game, and the totals other features read (PRD C12). The /games filters are in the website."""
from . import service  # noqa: F401  (registers the startng.json "category" section)
from .router import router  # noqa: F401

NAME = "catalog"
PERMISSIONS: dict[str, str] = {
    "ratings.moderate": "See game ratings and remove an abusive one",
}
