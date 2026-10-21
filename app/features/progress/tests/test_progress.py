from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.progress import service as progress
from app.features.progress.models import ActivityDay, Award, Run
from app.main import app
from app.models import Game, Tournament, User
from app.security import create_token

UTC = timezone.utc


@pytest.fixture(autouse=True)
def clean_registry():
    """Rules other tests registered must not leak into this one (and the other way round)."""
    saved = dict(progress._rules)
    progress._rules.clear()
    yield
    progress._rules.clear()
    progress._rules.update(saved)


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in ("ada", "tunde")]
        db.add_all(users)
        db.flush()
        db.add_all([Game(name=g, owner_id=users[0].id, repo_url=f"https://github.com/ada/{g}") for g in ("lanedash", "relic")])
        db.commit()
        ids = {u.name.lower(): u.id for u in users}
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
    yield Session, TestClient(app), tokens, ids
    app.dependency_overrides.clear()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "features", "progress")


def at(monkeypatch, when: datetime):
    monkeypatch.setattr(progress, "now", lambda: when)


# --- Nigeria-time days -------------------------------------------------------------------------------------------

def test_a_day_rolls_over_at_midnight_in_nigeria():
    assert progress.local_day(datetime(2026, 10, 20, 22, 59, tzinfo=UTC)) == date(2026, 10, 20)  # 23:59 in Lagos
    assert progress.local_day(datetime(2026, 10, 20, 23, 0, tzinfo=UTC)) == date(2026, 10, 21)  # 00:00 in Lagos


# --- streaks -----------------------------------------------------------------------------------------------------

D = date(2026, 10, 20)


def days(*offsets: int) -> list[date]:
    return [D + timedelta(days=o) for o in offsets]


def test_no_activity_means_no_streak():
    assert progress.streaks([], D) == (0, 0)


def test_consecutive_days_up_to_today_are_the_streak():
    assert progress.streaks(days(-2, -1, 0), D) == (3, 3)


def test_a_streak_stays_alive_until_the_end_of_the_next_day():
    assert progress.streaks(days(-2, -1), D) == (2, 2)  # not played today yet, still counts


def test_a_missed_day_ends_the_current_streak_but_keeps_the_best():
    assert progress.streaks(days(-3, -2), D) == (0, 2)


def test_best_streak_can_be_older_than_the_current_one():
    assert progress.streaks(days(-10, -9, -8, -1, 0), D) == (2, 3)


# --- XP and level ------------------------------------------------------------------------------------------------

def test_first_run_of_the_day_earns_bonus_xp():
    assert progress.xp_for_run(runs_today=0, xp_today=0) == progress.FIRST_RUN_XP + progress.RUN_XP


def test_later_runs_earn_the_normal_xp():
    assert progress.xp_for_run(runs_today=1, xp_today=12) == progress.RUN_XP


def test_xp_stops_at_the_daily_cap():
    cap = progress.DAILY_XP_CAP
    assert progress.xp_for_run(runs_today=40, xp_today=cap - 1) == 1
    assert progress.xp_for_run(runs_today=41, xp_today=cap) == 0


@pytest.mark.parametrize("xp,level", [(0, 1), (49, 1), (50, 2), (199, 2), (200, 3), (450, 4)])
def test_level_comes_from_xp(xp, level):
    assert progress.level_for(xp) == level


# --- recording a run ---------------------------------------------------------------------------------------------

def test_a_run_is_logged_and_counts_toward_the_snapshot(env):
    Session, _, _, ids = env
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 100})
        snap = progress.snapshot(db, ids["ada"])
    assert snap.runs == 1
    assert snap.distinct_games == 1
    assert (snap.current_streak, snap.best_streak) == (1, 1)
    assert snap.xp == progress.FIRST_RUN_XP + progress.RUN_XP
    assert snap.level == 1


def test_every_run_is_kept_not_only_personal_bests(env):
    Session, _, _, ids = env
    with Session() as db:
        for score in (100, 50, 70):
            progress.after_run(db, ids["ada"], "lanedash", {"score": score})
        assert db.scalar(select(func.count()).select_from(Run)) == 3
        assert progress.snapshot(db, ids["ada"]).runs == 3


def test_runs_are_per_player(env):
    Session, _, _, ids = env
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        assert progress.snapshot(db, ids["tunde"]).runs == 0


def test_distinct_games_counts_each_game_once(env):
    Session, _, _, ids = env
    with Session() as db:
        for game in ("lanedash", "lanedash", "relic"):
            progress.after_run(db, ids["ada"], game, {"score": 1})
        assert progress.snapshot(db, ids["ada"]).distinct_games == 2


def test_playing_on_three_days_in_a_row_builds_a_streak(env, monkeypatch):
    Session, _, _, ids = env
    start = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    with Session() as db:
        for n in range(3):
            at(monkeypatch, start + timedelta(days=n))
            progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        snap = progress.snapshot(db, ids["ada"])
    assert (snap.current_streak, snap.best_streak) == (3, 3)


def test_xp_is_capped_each_day(env, monkeypatch):
    Session, _, _, ids = env
    at(monkeypatch, datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    with Session() as db:
        for _ in range(progress.DAILY_XP_CAP):
            progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        assert progress.snapshot(db, ids["ada"]).xp == progress.DAILY_XP_CAP
        assert db.scalar(select(ActivityDay.xp).where(ActivityDay.user_id == ids["ada"])) == progress.DAILY_XP_CAP


def test_tournament_wins_come_from_settled_tournaments(env):
    Session, _, _, ids = env
    start = datetime(2026, 10, 1, tzinfo=UTC)
    with Session() as db:
        db.add(Tournament(starts_at=start, ends_at=start + timedelta(days=1), winner_id=ids["ada"], finished_at=start + timedelta(days=1)))
        db.commit()
        assert progress.snapshot(db, ids["ada"]).tournament_wins == 1
        assert progress.snapshot(db, ids["tunde"]).tournament_wins == 0


# --- rules and awards --------------------------------------------------------------------------------------------

def first_run_badge():
    return progress.Rule(kind="badge", key="first_score", check=lambda snap, run: snap.runs >= 1)


def test_a_registered_rule_awards_once_and_says_so(env):
    Session, _, _, ids = env
    progress.register("profiles", [first_run_badge()])
    with Session() as db:
        first = progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        second = progress.after_run(db, ids["ada"], "lanedash", {"score": 2})
        assert first.unlocked == [{"kind": "badge", "key": "first_score"}]
        assert second.unlocked == []
        assert db.scalar(select(func.count()).select_from(Award)) == 1


def test_three_features_can_register_rules_without_touching_the_engine(env):
    Session, _, _, ids = env
    progress.register("profiles", [progress.Rule("badge", "first_score", lambda s, r: s.runs >= 1)])
    progress.register("wardrobe", [progress.Rule("item", "explorer_cap", lambda s, r: s.distinct_games >= 2)])
    progress.register("leagues", [progress.Rule("badge", "ten_runs", lambda s, r: s.runs >= 10)])
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        out = progress.after_run(db, ids["ada"], "relic", {"score": 1})
        keys = {(a.kind, a.key) for a in db.scalars(select(Award))}
    assert keys == {("badge", "first_score"), ("item", "explorer_cap")}
    assert out.unlocked == [{"kind": "item", "key": "explorer_cap"}]


def test_a_rule_can_look_at_the_run_that_just_happened(env):
    Session, _, _, ids = env
    progress.register("x", [progress.Rule("badge", "big_score", lambda snap, run: run.game == "relic" and run.stats["score"] >= 100)])
    with Session() as db:
        assert progress.after_run(db, ids["ada"], "lanedash", {"score": 500}).unlocked == []
        assert progress.after_run(db, ids["ada"], "relic", {"score": 500}).unlocked == [{"kind": "badge", "key": "big_score"}]


def test_awards_belong_to_the_player_who_earned_them(env):
    Session, _, _, ids = env
    progress.register("profiles", [first_run_badge()])
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        assert progress.after_run(db, ids["tunde"], "lanedash", {"score": 1}).unlocked == [{"kind": "badge", "key": "first_score"}]


def test_a_broken_rule_does_not_break_scoring_or_other_rules(env):
    Session, _, _, ids = env

    def boom(snap, run):
        raise RuntimeError("bad rule")

    progress.register("broken", [progress.Rule("badge", "never", boom)])
    progress.register("profiles", [first_run_badge()])
    with Session() as db:
        out = progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
    assert out.unlocked == [{"kind": "badge", "key": "first_score"}]


def test_registering_the_same_feature_again_replaces_its_rules(env):
    Session, _, _, ids = env
    progress.register("profiles", [progress.Rule("badge", "old", lambda s, r: True)])
    progress.register("profiles", [progress.Rule("badge", "new", lambda s, r: True)])
    with Session() as db:
        assert progress.after_run(db, ids["ada"], "lanedash", {"score": 1}).unlocked == [{"kind": "badge", "key": "new"}]


# --- events ------------------------------------------------------------------------------------------------------

@pytest.fixture
def seen():
    got: list[tuple[str, dict]] = []
    handlers = {name: (lambda payload, name=name: got.append((name, payload))) for name in ("run_recorded", "award_unlocked")}
    for name, fn in handlers.items():
        events.subscribe(name)(fn)
    yield got
    for name, fn in handlers.items():
        events.unsubscribe(name, fn)


def test_a_run_publishes_run_recorded_with_its_id(env, seen):
    Session, _, _, ids = env
    with Session() as db:
        out = progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
    assert ("run_recorded", {"user_id": ids["ada"], "game": "lanedash", "run_id": out.run_id}) in seen


def test_a_new_award_publishes_award_unlocked_once(env, seen):
    Session, _, _, ids = env
    progress.register("profiles", [first_run_badge()])
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        progress.after_run(db, ids["ada"], "lanedash", {"score": 2})
    unlocked = [p for name, p in seen if name == "award_unlocked"]
    assert unlocked == [{"user_id": ids["ada"], "kind": "badge", "key": "first_score"}]


# --- the score route and the API ---------------------------------------------------------------------------------

def post_score(client, tokens, who, score, game="lanedash"):
    return client.post(f"/games/{game}/scores", json={"score": score}, headers=tokens[who])


def test_with_the_feature_off_scores_work_exactly_as_before(env):
    Session, client, tokens, ids = env
    progress.register("profiles", [first_run_badge()])
    res = post_score(client, tokens, "ada", 100)
    assert res.json() == {"best": 100, "improved": True, "rank": 1, "stats": {"score": 100}}
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0


def test_with_the_feature_on_a_score_logs_the_run_and_lists_new_awards(env, on):
    Session, client, tokens, ids = env
    progress.register("profiles", [first_run_badge()])
    res = post_score(client, tokens, "ada", 100)
    assert res.status_code == 200
    assert res.json() == {
        "best": 100, "improved": True, "rank": 1, "stats": {"score": 100},
        "unlocked": [{"kind": "badge", "key": "first_score"}],
    }
    assert post_score(client, tokens, "ada", 50).json()["unlocked"] == []
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 2


def test_a_worse_run_is_still_logged(env, on):
    Session, client, tokens, ids = env
    post_score(client, tokens, "ada", 100)
    assert post_score(client, tokens, "ada", 5).json()["improved"] is False
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 2


def test_progress_needs_sign_in(env, on):
    _, client, _, _ = env
    assert client.get("/me/progress").status_code == 401
    assert client.get("/me/awards").status_code == 401


def test_progress_is_404_while_the_feature_is_off(env):
    _, client, tokens, _ = env
    assert client.get("/me/progress", headers=tokens["ada"]).status_code == 404
    assert client.get("/me/awards", headers=tokens["ada"]).status_code == 404


def test_me_progress_returns_the_players_snapshot(env, on):
    _, client, tokens, _ = env
    post_score(client, tokens, "ada", 100)
    post_score(client, tokens, "ada", 90, game="relic")
    body = client.get("/me/progress", headers=tokens["ada"]).json()
    assert body == {
        "runs": 2, "distinct_games": 2, "current_streak": 1, "best_streak": 1, "streak_freezes": 0,
        "xp": progress.FIRST_RUN_XP + 2 * progress.RUN_XP, "level": 1, "tournament_wins": 0, "ranks": {},
    }


def test_me_awards_lists_what_the_player_owns(env, on):
    _, client, tokens, _ = env
    progress.register("profiles", [first_run_badge()])
    assert client.get("/me/awards", headers=tokens["ada"]).json() == []
    post_score(client, tokens, "ada", 100)
    awards = client.get("/me/awards", headers=tokens["ada"]).json()
    assert [(a["kind"], a["key"]) for a in awards] == [("badge", "first_score")]
    assert "earned_at" in awards[0]
    assert client.get("/me/awards", headers=tokens["tunde"]).json() == []
