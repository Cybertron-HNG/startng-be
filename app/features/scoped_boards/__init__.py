"""Scoped leaderboards (PRD Tr2): the same boards as the main ones, but only for the players in a crew. Friends joins them
when the friends list (S2) exists."""
from .router import router  # noqa: F401

NAME = "scoped_boards"  # the feature flag
PERMISSIONS: dict[str, str] = {}
