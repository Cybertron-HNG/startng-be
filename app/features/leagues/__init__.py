"""Leagues (PRD M2): three always-running tracks (Sprint 2 h, Daily 24 h, Weekly 7 days), each with its own tiers,
groups and history. A player's first unflagged run in a round puts them in it, in every track. See `service`."""
from . import service  # noqa: F401
from .router import router  # noqa: F401

NAME = "leagues"  # the feature flag
PERMISSIONS: dict[str, str] = {}
