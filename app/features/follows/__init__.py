"""Friends & follows (S2, owner: Seyi): who follows whom, and invite links that make players friends.
Other features call service.py (contract C6); see the top of that file."""
from .router import router  # noqa: F401

NAME = "follows"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {}  # no admin permissions
