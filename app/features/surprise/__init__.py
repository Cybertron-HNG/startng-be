"""Surprise me (Ziki #18): one random live game, for players who don't know where to start. Smaller games get a fair
chance, since the shelves favour the popular ones. GET /discover/surprise. 404 while the flag is off."""
from .router import router  # noqa: F401

NAME = "surprise"  # the feature flag
PERMISSIONS: dict[str, str] = {}
