"""Wardrobe: coins, items with rarities, a daily shop and game goals (the README's "Wardrobe" section).
Players earn coins and items by playing any start.ng game; items show on their one character everywhere.
Needs the `progress` flag (runs, streaks, ranks); `integrity` gives ranks for the top 10 and #1 rewards."""
from . import goals, rewards, rules, service  # noqa: F401  (the startng.json section, event handlers, award rules)
from .router import router  # noqa: F401

NAME = "wardrobe"  # the feature flag
PERMISSIONS: dict[str, str] = {
    "wardrobe.manage": "Give or take back coins and items",
}
