"""F10 support: signed-in players report problems with start.ng itself; admins sort them, and a real bug pays the
reporter coins (once, after an admin confirms it). The AI answers (from our docs) come later."""
from .router import router  # noqa: F401

NAME = "support"
PERMISSIONS: dict[str, str] = {
    "support.reports": "See problem reports about start.ng and mark real bugs (which pays the reporter coins)",
}
