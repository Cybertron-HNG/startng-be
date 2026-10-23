"""Tags (PRD workstream 2, Chloe): many tags per game ("Nigerian themed", "Endless runners", "Brainy games"), a listing
page for each tag, and an admin review so a person approves every tag. Tags, not folders: a game can have any number.
Other features read service.py. T1's single category (features/catalog) is separate and untouched."""
from . import service  # noqa: F401
from .router import router  # noqa: F401

NAME = "tags"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {
    "tags.manage": "Suggest tags for games and approve or change them",
}
