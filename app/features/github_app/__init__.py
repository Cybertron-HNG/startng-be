"""F1 the start.ng GitHub App: a push to a game's repo updates the game straight away (signed webhook)."""
from .router import router  # noqa: F401

NAME = "github_app"
PERMISSIONS: dict[str, str] = {}
