"""Items the Progress Engine can decide from a player's snapshot. They're awarded once, after the run that earns
them, and show in the score reply's `unlocked` (so the Kit's end-of-round card shows them). Players who already
qualify get theirs on their next run."""
from ... import flags
from ..progress import service as progress


def _on() -> bool:
    from . import NAME
    return flags.is_on(NAME)


RULES = [
    progress.Rule("item", "explorer_cap", lambda snap, run: _on() and snap.distinct_games >= 5),
    progress.Rule("item", "streak_scarf", lambda snap, run: _on() and snap.best_streak >= 7),
    progress.Rule("item", "flame_aura", lambda snap, run: _on() and snap.best_streak >= 30),
    progress.Rule("item", "veteran_jacket", lambda snap, run: _on() and snap.level >= 10),
]

progress.register("wardrobe", RULES)
