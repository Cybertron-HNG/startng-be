"""Tip a creator (Ziki #82): a player sends 10, 25 or 50 of the coins they earned to the creator of a game they played,
with an optional short thank-you. The creator sees each tip and note in Your games and spends the coins like any others,
and the game's page shows how many people support its creator. Limits stop farming: never your own game, only games you
played, at most 3 tips and 100 coins a day, and never coins from admins or from tips you were given."""
from .router import router  # noqa: F401

NAME = "tips"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {}
