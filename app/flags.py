"""Feature flags. Every new feature ships dark: its routes answer 404 until it is switched on.

It is switched on in either of two places, and the second wins:

    FEATURES=wardrobe,profiles      the server setting: only these are on ("*" turns everything on, for local testing)
    the admin page's Features tab   a super admin switches one feature on or off, no redeploy (table feature_switches)

The website asks GET /features for the list, so it can hide what's off.
"""
import time

from fastapi import HTTPException, status
from sqlalchemy import select

from .config import settings

CACHE_SECONDS = 10  # flags are checked on every request, so the switches are read at most this often
_cache: dict = {"at": -1e9, "switches": {}}


def switches() -> dict[str, bool]:
    """What admins switched in the admin page: {feature: on}. Empty when there are none (or the table isn't there yet)."""
    if not settings.feature_switches:
        return {}
    now = time.monotonic()
    if now - _cache["at"] > CACHE_SECONDS:
        try:
            from .db import SessionLocal
            from .models import FeatureSwitch

            with SessionLocal() as db:
                _cache["switches"] = {r.feature: r.enabled for r in db.scalars(select(FeatureSwitch))}
        except Exception:  # no database or table yet: the FEATURES setting alone decides
            _cache["switches"] = {}
        _cache["at"] = now
    return _cache["switches"]


def forget() -> None:
    """Read the switches again on the next check (after one changed)."""
    _cache["at"] = -1e9


def env_features() -> set[str]:
    """The FEATURES setting, as written ("*" included)."""
    return {f.strip().lower() for f in settings.features.split(",") if f.strip()}


def env_on(name: str) -> bool:
    on = env_features()
    return "*" in on or name.lower() in on


def enabled_features() -> set[str]:
    """The features that are on. {"*"} when everything is (no switch has turned one off)."""
    on, mine = env_features(), switches()
    off = {n for n, v in mine.items() if not v}
    if "*" in on:
        if not off:
            return {"*"}
        from . import features

        return (set(features.FEATURES) | {n for n, v in mine.items() if v}) - off
    return (on | {n for n, v in mine.items() if v}) - off


def is_on(name: str) -> bool:
    mine = switches()
    if name.lower() in mine:
        return mine[name.lower()]
    return env_on(name)


def require(name: str):
    """A route dependency: 404 while the feature is off, as if the route didn't exist."""
    def dependency() -> None:
        if not is_on(name):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    return dependency
