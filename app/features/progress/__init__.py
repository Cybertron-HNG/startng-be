"""The Progress Engine (PRD D1): the run log, daily activity and streaks, XP and level, and the awards other features
register rules for. Other features use `service` (`after_run`, `snapshot`, `register`); see its docstring."""
from . import service  # noqa: F401
from .router import router  # noqa: F401

NAME = "progress"  # the feature flag
PERMISSIONS: dict[str, str] = {}
