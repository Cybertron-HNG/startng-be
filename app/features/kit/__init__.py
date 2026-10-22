"""The start.ng Kit (PRD M1): how long people play. Games that load https://start.ng/kit.js ping start.ng's /connect
page when a session starts, every 30 s while the game is on screen, and when it ends; /connect passes the pings on
here. Other features read the result through service.py (contract C11: T2's creator dashboard, A1's ranking)."""
from .router import router  # noqa: F401

NAME = "kit"  # the feature flag
PERMISSIONS: dict[str, str] = {}
