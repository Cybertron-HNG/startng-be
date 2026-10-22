"""H2 duplicate finder: spots games that look like copies of each other, for admins to hide, delete or keep."""
from .router import router  # noqa: F401

NAME = "duplicates"
PERMISSIONS: dict[str, str] = {
    "games.duplicates": "See possible duplicate games, and hide, delete or keep them",
}
