"""Tags (PRD workstream 2, Chloe): many tags per game ("Nigerian themed", "Endless runners", "Brainy games"), a listing
page for each tag, and an admin review so a person approves every tag. Tags, not folders: a game can have any number.
Other features read service.py. T1's single category (features/catalog) is separate and untouched."""
from ... import events
from . import service  # noqa: F401
from .router import router  # noqa: F401

NAME = "tags"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    "tags.manage": "Suggest tags for games and approve or change them",
}

# Tags work on their own: a background job suggests (and, apart from the tags that need a person, approves) tags for every game
# that has none, and again whenever a game is published or gets a new version.
TASKS = [service.run_forever]
events.subscribe("game_published")(service.on_game_changed)
events.subscribe("game_updated")(service.on_game_changed)
