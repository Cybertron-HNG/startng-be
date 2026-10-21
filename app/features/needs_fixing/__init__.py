"""H2 "needs fixing": an admin marks a broken game; it's hidden until its owner fixes it and an admin puts it back."""
from .router import router  # noqa: F401

NAME = "needs_fixing"
PERMISSIONS: dict[str, str] = {
    "games.needs_fixing": "Mark games as needing a fix (hides them) and put them back once fixed",
}
