"""A2 spotlight: a Game of the Week (picked by admins, read by the weekly digest) and a daily challenge: one personal goal a
day on one game, worked out from recent runs, that a player completes once and that earns a badge through D1."""
from . import service  # noqa: F401  (registers the run_recorded handler and the D1 badge rule)
from .router import router  # noqa: F401

NAME = "spotlight"
PERMISSIONS: dict[str, str] = {
    "spotlight.edit": "Choose the Game of the Week",
}
