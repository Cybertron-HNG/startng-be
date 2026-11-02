"""What other features may call (this feature's contract): pick()."""
import random

from sqlalchemy.orm import Session

from ..discovery import service as discovery
from ..tags import service as tags

NEVER = {"hangout"}  # start.ng's own space, not a game to be surprised by
MAX_TAG_PAGES = 20  # a category's games are read a page at a time; far more than any category has today


def _in_tag(db: Session, slug: str) -> set[str]:
    """The names of the games in a category with a public page. Raises tags.TagError(404) for an unknown one."""
    names: set[str] = set()
    for page in range(1, MAX_TAG_PAGES + 1):
        _, total, games = tags.games_of(db, slug, page)
        names |= {g.name for g in games}
        if not games or len(names) >= total:
            break
    return names


def pick(db: Session, user_id: int | None = None, tag: str | None = None, exclude: str | None = None,
         rng: random.Random | None = None) -> str | None:
    """A random live game: in the category if one is given, never `exclude` (the game the player is on), and, for a
    signed-in player, one they haven't opened lately when there is one. None when there's nothing to pick."""
    pool = [g.name for g in discovery.visible(db) if g.name not in NEVER and g.name != exclude]
    if tag:
        wanted = _in_tag(db, tag)
        pool = [n for n in pool if n in wanted]
    if user_id is not None:
        recent = set(discovery.continue_playing(db, user_id))
        pool = [n for n in pool if n not in recent] or pool  # everything opened lately: any game will do
    return (rng or random).choice(pool) if pool else None
