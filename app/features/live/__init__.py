"""Live (Ziki #37): real signs of play, so start.ng doesn't look empty. "N playing now" on games (only from 2 players),
recent moments ("Ada took #1 on Danfo Make Way"), and how many are on today's tournament games. GET /live.
Players with a private profile are counted but never named."""
from . import service  # noqa: F401  (subscribes to record_beaten)
from .router import router  # noqa: F401

NAME = "live"  # the feature flag
PERMISSIONS: dict[str, str] = {}
