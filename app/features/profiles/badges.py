"""The badge catalog (S1) and the rules that award them, registered with the Progress Engine (D1). A badge is awarded
once per player, after the run that earns it, and shows on their page. Players who already qualify get theirs on their
next run."""
from dataclasses import dataclass

from ..progress import service as progress


@dataclass(frozen=True)
class Badge:
    key: str
    name: str
    description: str


CATALOG: list[Badge] = [
    Badge("first_score", "First score", "Played a game and sent in a score"),
    Badge("streak_3", "3-day streak", "Played three days in a row"),
    Badge("streak_7", "7-day streak", "Played seven days in a row"),
    Badge("top_10", "Top 10", "Held a place in the top 10 of a game"),
    Badge("tournament_winner", "Tournament winner", "Won a daily tournament"),
    Badge("ten_games", "Explorer", "Tried 10 different games"),
]
BY_KEY = {b.key: b for b in CATALOG}

RULES = [
    progress.Rule("badge", "first_score", lambda snap, run: snap.runs >= 1),
    progress.Rule("badge", "streak_3", lambda snap, run: snap.best_streak >= 3),
    progress.Rule("badge", "streak_7", lambda snap, run: snap.best_streak >= 7),
    progress.Rule("badge", "top_10", lambda snap, run: any(place <= 10 for place in snap.ranks.values())),
    progress.Rule("badge", "tournament_winner", lambda snap, run: snap.tournament_wins >= 1),
    progress.Rule("badge", "ten_games", lambda snap, run: snap.distinct_games >= 10),
]

progress.register("profiles", RULES)
