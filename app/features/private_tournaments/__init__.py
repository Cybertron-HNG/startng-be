"""Private tournaments: friends pick up to five games and a window, share an invite link, and race on their own
standings (PRD Tr1). Points use the daily tournament's 10/7/5/3/1 rule, but nothing here gives global points, XP or
rewards, so friends can't farm them."""
from fastapi import APIRouter

from . import admin_router, service  # noqa: F401  (service subscribes to score_submitted)
from .router import router as _players

NAME = "private_tournaments"  # the feature flag
PERMISSIONS: dict[str, str] = {
    "tournaments.private": "Make private tournaments to share by link (only people who register see them)",  # F9
}

router = APIRouter()
router.include_router(_players)
router.include_router(admin_router.router)
