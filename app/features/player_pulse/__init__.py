"""Player Pulse (Ziki #58): after a round, a player says how it felt (loved it, too hard, too easy, plus an optional
short note), and the game's creator sees a private summary. One answer per player, game and day; a new answer that day
replaces the old one. Players are never named to the creator."""
from .router import router  # noqa: F401

NAME = "player_pulse"  # the feature flag: its routes answer 404 until FEATURES includes it
PERMISSIONS: dict[str, str] = {}
