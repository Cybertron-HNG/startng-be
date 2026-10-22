"""Private tournaments: friends pick up to five games and a window, share an invite link, and race on their own
standings (PRD Tr1). Points use the daily tournament's 10/7/5/3/1 rule, but nothing here gives global points, XP or
rewards, so friends can't farm them."""
from . import service  # noqa: F401  (subscribes to score_submitted)
from .router import router  # noqa: F401

NAME = "private_tournaments"  # the feature flag
PERMISSIONS: dict[str, str] = {}
