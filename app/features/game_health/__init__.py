"""H2 game health: a background job checks every live game; one that keeps failing is unhealthy, shown to admins and
its owner, and game_unhealthy is published."""
from . import service
from .router import router  # noqa: F401

NAME = "game_health"
PERMISSIONS: dict[str, str] = {
    "games.health": "See game health checks and check games again",
}
TASKS = [service.run_forever]  # the background checker
