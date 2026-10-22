"""Where coins and earned items come from. Everything reacts to events, so no game and no other feature changes.

Playing (capped at service.DAILY_CAP a day):
    first run of the day 10 · a game you've never played 25 · a game's own goals (goals.py decides how much)
Big moments (not capped):
    a 3/7/30-day streak 30/100/500 · each new level, level x 10 · top 10 on a game 150 and #1 on it 400 (once per game,
    less on small boards) · a daily tournament's top 3 300/200/100 (less with few players) · beating a challenge 50 ·
    a league promotion 75
Items: crowns for holding a top place on a busy board, the cape for winning a tournament, the sash for beating a
challenge. The rest (streaks, games tried, level) are Progress Engine rules in rules.py, so they show on the Kit's
end-of-round card.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ... import events, flags
from ...db import SessionLocal
from ...models import TournamentPoints
from ..progress import service as progress
from ..progress.models import Run  # the run log, read only (as leagues and challenges do)
from . import goals as game_goals
from . import service
from .models import CoinEntry, OwnedItem

FIRST_RUN = 10
NEW_GAME = 25
STREAKS = {3: 30, 7: 100, 30: 500}
LEVEL = 10  # x the new level
TOP_10 = 150
FIRST_PLACE = 400
FULL_BOARD = 30  # players a board needs for full rank coins and for the crowns
TOURNAMENT = {1: 300, 2: 200, 3: 100}
FULL_TOURNAMENT = 10  # players a tournament needs for full coins
CHALLENGE = 50
PROMOTION = 75


def _on() -> bool:
    from . import NAME
    return flags.is_on(NAME)


def board_factor(players: int) -> float:
    """Ranks on a small board are easy, so they pay less: full from FULL_BOARD players."""
    return min(1.0, players / FULL_BOARD)


def reward_run(db, run) -> None:
    """Everything one unflagged run earns. Each payment is once per its ref, so seeing a run twice pays nothing more."""
    uid, game = run.user_id, run.game_name
    day = progress.local_day(run.created_at).isoformat()

    service.pay(db, uid, FIRST_RUN, "first_run", day, capped=True)
    earlier = db.scalar(select(Run.id).where(Run.user_id == uid, Run.game_name == game, Run.flagged.is_(False),
                                             Run.id < run.id).limit(1))
    if earlier is None:
        service.pay(db, uid, NEW_GAME, "new_game", game, capped=True)

    from_game = db.scalar(select(func.coalesce(func.sum(CoinEntry.amount), 0)).where(
        CoinEntry.user_id == uid, CoinEntry.reason == "game_goal", CoinEntry.day == progress.local_day(run.created_at),
        CoinEntry.ref.like(f"{game}:%"))) or 0
    for goal in game_goals.of_game(db, game):
        if from_game >= game_goals.GAME_DAILY_CAP or not game_goals.met(goal, run.stats or {}):
            continue
        _, coins, _ = game_goals.difficulty(db, goal)
        ref = f"{game}:{goal.key}" + (f":{day}" if goal.daily else "")
        from_game += service.pay(db, uid, min(coins, game_goals.GAME_DAILY_CAP - from_game), "game_goal", ref, capped=True)

    snap = progress.snapshot(db, uid)
    if snap.current_streak in STREAKS:
        service.pay(db, uid, STREAKS[snap.current_streak], "streak", f"{snap.current_streak}:{day}")
    if snap.level > 1:
        service.pay(db, uid, snap.level * LEVEL, "level", str(snap.level))

    place = snap.ranks.get(game)  # held across the player's last two runs, like the Top 10 badge
    if place is not None:
        players = game_goals.players_of(db, game)
        factor = board_factor(players)
        if place <= 10 and players > 10:
            service.pay(db, uid, round(TOP_10 * factor), "top_10", game)
        if place == 1 and players >= 5:
            service.pay(db, uid, round(FIRST_PLACE * factor), "first_place", game)
        if players >= FULL_BOARD and place <= 10:
            service.grant(db, uid, "silver_crown")
        if players >= FULL_BOARD and place == 1:
            service.grant(db, uid, "gem_crown")


@events.subscribe("run_recorded")
def _on_run(payload: dict) -> None:
    """Every unflagged run, and every flagged one once an admin clears it. Held runs never publish this, so they earn
    nothing while they're held."""
    if not _on():
        return
    with SessionLocal() as db:
        run = db.get(Run, payload["run_id"])
        if run is not None and not run.flagged:
            reward_run(db, run)


@events.subscribe("tournament_finished")
def _on_tournament(payload: dict) -> None:
    if not _on():
        return
    factor = min(1.0, payload["players"] / FULL_TOURNAMENT)
    with SessionLocal() as db:
        top = db.scalars(select(TournamentPoints).where(
            TournamentPoints.tournament_id == payload["tournament_id"], TournamentPoints.place <= 3)).all()
        for row in top:
            service.pay(db, row.user_id, round(TOURNAMENT[row.place] * factor), "tournament", str(payload["tournament_id"]))
        if payload["winner_id"] is not None:
            service.grant(db, payload["winner_id"], "tournament_cape")


@events.subscribe("challenge_beaten")
def _on_challenge(payload: dict) -> None:
    if not _on():
        return
    with SessionLocal() as db:
        service.pay(db, payload["user_id"], CHALLENGE, "challenge", str(payload["challenge_id"]))
        service.grant(db, payload["user_id"], "champion_sash")


@events.subscribe("league_promoted")
def _on_promotion(payload: dict) -> None:
    if not _on():
        return
    with SessionLocal() as db:
        service.pay(db, payload["user_id"], PROMOTION, "league", f"{payload['track']}:{payload['tier']}:{service.today()}")


@events.subscribe("award_unlocked")
def _on_award(payload: dict) -> None:
    """Items the Progress Engine awarded (rules.py) already count as owned; keeping a copy here makes the
    "owned by n% of players" figures one query."""
    if not _on() or payload["kind"] != "item":
        return
    with SessionLocal() as db:
        if db.scalar(select(OwnedItem.id).where(OwnedItem.user_id == payload["user_id"], OwnedItem.item_key == payload["key"])) is None:
            db.add(OwnedItem(user_id=payload["user_id"], item_key=payload["key"], source="earned"))
            try:
                db.commit()
            except IntegrityError:  # added at the same moment by another request
                db.rollback()
