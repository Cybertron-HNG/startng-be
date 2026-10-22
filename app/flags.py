"""Feature flags. Every new feature ships dark: its routes answer 404 until its name is in FEATURES.

    FEATURES=wardrobe,profiles      only these are on
    FEATURES=*                      everything is on (local testing)

The website asks GET /features for the list, so it can hide what's off.
"""
from fastapi import HTTPException, status

from .config import settings


def enabled_features() -> set[str]:
    return {f.strip().lower() for f in settings.features.split(",") if f.strip()}


def is_on(name: str) -> bool:
    on = enabled_features()
    return "*" in on or name.lower() in on


def require(name: str):
    """A route dependency: 404 while the feature is off, as if the route didn't exist."""
    def dependency() -> None:
        if not is_on(name):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return dependency
