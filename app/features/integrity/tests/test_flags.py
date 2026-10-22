"""Score integrity, second drop: outlier flags, the review queue, the rank-held rule and record_beaten."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import admin as adm
from app import events, tournaments
from app.config import settings
from app.features.integrity import service as integrity
from app.features.integrity.models import ScoreFlag
from app.features.progress import service as progress
from app.features.progress.models import ActivityDay, Run
from app.models import Activity, Score, Tournament, TournamentEntry, TournamentGame, TournamentScore, User
from .test_integrity import ARENA, BASE, env, fresh_state, post, seed  # noqa: F401  (shared fixtures and helpers)
from app.features.progress.tests.test_progress import clean_registry  # noqa: F401

UTC = timezone.utc


@pytest.fixture
def both(monkeypatch):
    """Score integrity and the Progress Engine, which flagged runs need (they are kept in the run log)."""
    monkeypatch.setattr(settings, "features", "integrity,progress")
    monkeypatch.setattr(settings, "scores_per_minute", 1000)


def give(db, uid, game, best):
    """A player who already has a best score of `best` in a plain-score game."""
    db.add(Score(user_id=uid, game_name=game, best=best, stats={"score": best}, updated_at=BASE))
    db.commit()


def crowd(db, ids, game="lanedash", n=30, low=100):
    """n players with scores low..low+n-1, so the game has a 99th percentile to judge by."""
    for k, uid in enumerate(ids[1:n + 1]):
        give(db, uid, game, low + k)


# --- spotting an outlier -----------------------------------------------------------------------------------------

def reason(db, game, value, previous, stats=None):
    schema = integrity._schema(db, game)
    return integrity.outlier_reason(db, game, schema, stats or {"score": value}, previous)


def test_more_than_triple_your_previous_best_is_flagged(env):
    Session, _, _, _ = env
    with Session() as db:
        assert reason(db, "lanedash", 301, 100) is not None


def test_exactly_triple_your_previous_best_is_fine(env):
    Session, _, _, _ = env
    with Session() as db:
        assert reason(db, "lanedash", 300, 100) is None


def test_a_small_previous_best_is_not_judged(env):
    Session, _, _, _ = env
    with Session() as db:  # 10 is under flag_min_previous, so going to 100 is just a beginner improving
        assert reason(db, "lanedash", 100, 10) is None


def test_a_first_run_with_no_crowd_to_compare_with_is_not_flagged(env):
    Session, _, _, _ = env
    with Session() as db:
        assert reason(db, "lanedash", 9_999_999, None) is None


def test_far_beyond_the_games_99th_percentile_is_flagged(env):
    Session, _, _, ids = env
    with Session() as db:
        crowd(db, ids)  # 100..129; the 99th percentile is 129
        assert reason(db, "lanedash", 646, None) is not None  # more than 5 x 129
        assert reason(db, "lanedash", 645, None) is None


def test_too_few_players_to_judge_by_percentile(env):
    Session, _, _, ids = env
    with Session() as db:
        crowd(db, ids, n=10)  # fewer than flag_min_players
        assert reason(db, "lanedash", 9_999_999, None) is None


def test_games_where_lower_is_better_flag_impossibly_small_values(env):
    Session, _, _, ids = env
    with Session() as db:
        db.add(Score(user_id=ids[1], game_name="speedrun", best=60, stats={"time": 60, "score": 5}, updated_at=BASE))
        db.commit()
        assert reason(db, "speedrun", 19, 60, {"time": 19, "score": 5}) is not None  # under a third of their best
        assert reason(db, "speedrun", 20, 60, {"time": 20, "score": 5}) is None


def test_the_thresholds_are_settings(env, monkeypatch):
    Session, _, _, _ = env
    monkeypatch.setattr(settings, "flag_previous_factor", 2)
    with Session() as db:
        assert reason(db, "lanedash", 201, 100) is not None


# --- what happens to a flagged run -------------------------------------------------------------------------------

def test_a_flagged_run_is_saved_and_flagged_but_never_reaches_the_leaderboard(env, both):
    Session, client, tokens, ids = env
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
    res = post(client, tokens, ids[1], "lanedash", score=1000)
    assert res.status_code == 200
    assert client.get("/games/lanedash/leaderboard").json()[0]["score"] == 100
    with Session() as db:
        flag = db.scalars(select(ScoreFlag)).one()
        assert (flag.status, flag.user_id, flag.game_name, flag.stats, flag.previous_best) == ("open", ids[1], "lanedash", {"score": 1000}, 100)
        assert "previous best" in flag.reason
        assert [r.flagged for r in db.scalars(select(Run).order_by(Run.id))] == [True]
        assert flag.run_id == db.scalars(select(Run)).one().id
        assert db.scalar(select(Score.best).where(Score.user_id == ids[1])) == 100


def test_the_response_to_a_flagged_run_looks_like_any_other(env, both):
    Session, client, tokens, ids = env
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
    body = post(client, tokens, ids[1], "lanedash", score=1000).json()
    assert body == {"best": 100, "improved": False, "rank": 1, "stats": {"score": 100}, "unlocked": []}


def test_a_flagged_first_run_makes_no_score_but_still_gets_an_answer(env, both):
    Session, client, tokens, ids = env
    with Session() as db:
        crowd(db, ids)
    res = post(client, tokens, ids[35], "lanedash", score=100_000)
    assert res.status_code == 200 and res.json()["improved"] is False and res.json()["rank"] >= 1
    with Session() as db:
        assert db.scalar(select(Score).where(Score.user_id == ids[35])) is None
        assert db.scalar(select(func.count()).select_from(ScoreFlag)) == 1


def test_a_flagged_run_does_not_count_in_the_daily_tournament(env, both, monkeypatch):
    Session, client, tokens, ids = env
    monkeypatch.setattr(tournaments, "now", lambda: datetime(2026, 10, 20, 20, 0, tzinfo=UTC))
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
        t = Tournament(starts_at=datetime(2026, 10, 20, 17, 0, tzinfo=UTC), ends_at=datetime(2026, 10, 21, 17, 0, tzinfo=UTC))
        db.add(t)
        db.flush()
        db.add_all([TournamentGame(tournament_id=t.id, game_name="lanedash", position=1), TournamentEntry(tournament_id=t.id, user_id=ids[1])])
        db.commit()
    post(client, tokens, ids[1], "lanedash", score=1000)
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(TournamentScore)) == 0


def test_a_flagged_run_gives_no_xp_award_or_event(env, both):
    Session, client, tokens, ids = env
    progress.register("profiles", [progress.Rule("badge", "any", lambda s, r: True)])
    got = []
    handlers = {n: (lambda p, n=n: got.append(n)) for n in ("score_submitted", "run_recorded", "award_unlocked", "record_beaten")}
    for n, fn in handlers.items():
        events.subscribe(n)(fn)
    try:
        with Session() as db:
            give(db, ids[1], "lanedash", 100)
        post(client, tokens, ids[1], "lanedash", score=1000)
    finally:
        for n, fn in handlers.items():
            events.unsubscribe(n, fn)
    assert got == []
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ActivityDay)) == 0
        assert progress.awards_of(db, ids[1]) == []
        snap = progress.snapshot(db, ids[1])
        assert (snap.runs, snap.xp, snap.distinct_games) == (0, 0, 0)


def test_nothing_is_flagged_while_the_feature_is_off(env, monkeypatch):
    Session, client, tokens, ids = env
    monkeypatch.setattr(settings, "features", "")
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
    post(client, tokens, ids[1], "lanedash", score=1000)
    assert client.get("/games/lanedash/leaderboard").json()[0]["score"] == 1000
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(ScoreFlag)) == 0


def test_without_the_progress_engine_a_flagged_run_is_still_held_back(env, monkeypatch):
    Session, client, tokens, ids = env
    monkeypatch.setattr(settings, "features", "integrity")
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
    post(client, tokens, ids[1], "lanedash", score=1000)
    with Session() as db:
        flag = db.scalars(select(ScoreFlag)).one()
        assert flag.run_id is None and flag.status == "open"
    assert client.get("/games/lanedash/leaderboard").json()[0]["score"] == 100


# --- the rank-held rule ------------------------------------------------------------------------------------------

def test_a_rank_only_counts_once_it_has_been_held_across_two_runs(env, both):
    Session, client, tokens, ids = env
    post(client, tokens, ids[1], "lanedash", score=100)
    with Session() as db:
        assert progress.snapshot(db, ids[1]).ranks == {}  # one run is not enough
    post(client, tokens, ids[1], "lanedash", score=90)
    with Session() as db:
        assert progress.snapshot(db, ids[1]).ranks == {"lanedash": 1}


def test_the_worse_of_the_last_two_ranks_is_the_one_that_counts(env, both):
    Session, client, tokens, ids = env
    post(client, tokens, ids[1], "lanedash", score=100)  # rank 1
    post(client, tokens, ids[2], "lanedash", score=200)
    post(client, tokens, ids[3], "lanedash", score=300)
    post(client, tokens, ids[1], "lanedash", score=100)  # still 100, now rank 3
    with Session() as db:
        assert progress.snapshot(db, ids[1]).ranks == {"lanedash": 3}


def test_ranks_are_kept_per_game_and_flagged_runs_do_not_count(env, both):
    Session, client, tokens, ids = env
    for score in (100, 90):
        post(client, tokens, ids[1], "lanedash", score=score)
    post(client, tokens, ids[1], "lanedash", score=100_000)  # flagged: not part of the last two
    post(client, tokens, ids[1], "capped", score=10)
    with Session() as db:
        assert progress.snapshot(db, ids[1]).ranks == {"lanedash": 1}


# --- record_beaten -----------------------------------------------------------------------------------------------

@pytest.fixture
def beaten():
    got = []
    fn = got.append
    events.subscribe("record_beaten")(fn)
    yield got
    events.unsubscribe("record_beaten", fn)


def test_taking_the_top_spot_announces_the_old_record_holder(env, both, beaten):
    _, client, tokens, ids = env
    post(client, tokens, ids[1], "lanedash", score=100)
    post(client, tokens, ids[2], "lanedash", score=150)
    assert beaten == [{"game": "lanedash", "user_id": ids[2], "previous_user_id": ids[1]}]


def test_the_first_score_ever_beats_nobody(env, both, beaten):
    _, client, tokens, ids = env
    post(client, tokens, ids[1], "lanedash", score=100)
    assert beaten == []


def test_improving_your_own_record_or_coming_second_announces_nothing(env, both, beaten):
    _, client, tokens, ids = env
    post(client, tokens, ids[1], "lanedash", score=100)
    post(client, tokens, ids[1], "lanedash", score=120)  # own record
    post(client, tokens, ids[2], "lanedash", score=110)  # second place
    assert beaten == []


def test_a_flagged_run_cannot_take_the_top_spot(env, both, beaten):
    Session, client, tokens, ids = env
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
        give(db, ids[2], "lanedash", 90)
    post(client, tokens, ids[2], "lanedash", score=100_000)
    assert beaten == []


# --- the review queue --------------------------------------------------------------------------------------------

def make_admin(Session, monkeypatch, uid, role_permissions=None, ids=None):
    """Make player `uid` an admin the way the app does it: a Google account whose email is a super admin's."""
    with Session() as db:
        user = db.get(User, uid)
        user.google_sub = f"g-{uid}"
        db.commit()
        email = user.email
    if role_permissions is None:
        monkeypatch.setattr(settings, "super_admin_emails", email)
    return email


def flagged_game(env, monkeypatch, who=1):
    """A flag on lanedash for player ids[who], plus a super admin (ids[10])."""
    Session, client, tokens, ids = env
    monkeypatch.setattr(settings, "features", "integrity,progress")
    monkeypatch.setattr(settings, "scores_per_minute", 1000)
    with Session() as db:
        give(db, ids[who], "lanedash", 100)
    post(client, tokens, ids[who], "lanedash", score=1000)
    make_admin(Session, monkeypatch, ids[10])
    with Session() as db:
        return db.scalars(select(ScoreFlag)).one().id


def test_the_permission_is_registered_so_roles_can_include_it():
    from app.features import load
    load()
    assert "scores.review" in adm.PERMISSIONS


def test_the_game_owner_sees_the_flags_on_their_game(env, both):
    Session, client, tokens, ids = env
    with Session() as db:
        give(db, ids[1], "lanedash", 100)
    post(client, tokens, ids[1], "lanedash", score=1000)
    res = client.get("/games/lanedash/flags", headers=tokens[ids[0]])  # ids[0] owns lanedash
    assert res.status_code == 200
    (item,) = res.json()
    assert (item["status"], item["player_id"], item["stats"], item["previous_best"]) == ("open", ids[1], {"score": 1000}, 100)
    assert "previous best" in item["reason"] and "@" not in res.text


def test_other_players_cannot_see_a_games_flags(env, both):
    _, client, tokens, ids = env
    assert client.get("/games/lanedash/flags", headers=tokens[ids[1]]).status_code == 404
    assert client.get("/games/lanedash/flags").status_code == 401
    assert client.get("/games/nope/flags", headers=tokens[ids[0]]).status_code == 404


def test_the_flags_are_404_while_the_feature_is_off(env):
    _, client, tokens, ids = env
    assert client.get("/games/lanedash/flags", headers=tokens[ids[0]]).status_code == 404


def test_admins_with_the_permission_list_the_flags(env, monkeypatch):
    Session, client, tokens, ids = env
    flag_id = flagged_game(env, monkeypatch)
    res = client.get("/admin/score-flags", headers=tokens[ids[10]])
    assert res.status_code == 200
    assert [f["id"] for f in res.json()] == [flag_id]
    assert res.json()[0]["game"] == "lanedash" and res.json()[0]["player"]
    assert client.get("/admin/score-flags?status=cleared", headers=tokens[ids[10]]).json() == []


def test_players_who_are_not_admins_get_a_404(env, monkeypatch):
    _, client, tokens, ids = env
    flagged_game(env, monkeypatch)
    assert client.get("/admin/score-flags", headers=tokens[ids[11]]).status_code == 404


def test_an_admin_without_the_permission_is_refused(env, monkeypatch):
    Session, client, tokens, ids = env
    flagged_game(env, monkeypatch)
    role = client.post("/admin/roles", json={"name": "viewer", "permissions": ["reports.view"]}, headers=tokens[ids[10]]).json()
    with Session() as db:
        db.get(User, ids[12]).google_sub = "g-12"
        email = db.get(User, ids[12]).email
        db.commit()
    added = client.post("/admin/admins", json={"email": email, "role_id": role["id"]}, headers=tokens[ids[10]])
    assert added.status_code == 201
    assert client.get("/admin/score-flags", headers=tokens[ids[12]]).status_code == 403
    role2 = client.post("/admin/roles", json={"name": "scorer", "permissions": ["scores.review"]}, headers=tokens[ids[10]]).json()
    assert client.put(f"/admin/admins/{added.json()['id']}", json={"role_id": role2["id"]}, headers=tokens[ids[10]]).status_code == 200
    assert client.get("/admin/score-flags", headers=tokens[ids[12]]).status_code == 200


def test_clearing_a_flag_puts_the_run_back_on_the_board_and_counts_it_again(env, monkeypatch):
    Session, client, tokens, ids = env
    progress.register("profiles", [progress.Rule("badge", "any_run", lambda s, r: s.runs >= 1)])
    flag_id = flagged_game(env, monkeypatch)
    res = client.post(f"/admin/score-flags/{flag_id}/clear", json={"note": "legit, checked the replay"}, headers=tokens[ids[10]])
    assert res.status_code == 200 and res.json()["status"] == "cleared"
    assert client.get("/games/lanedash/leaderboard").json()[0]["score"] == 1000
    with Session() as db:
        flag = db.get(ScoreFlag, flag_id)
        assert flag.note == "legit, checked the replay" and flag.reviewed_at is not None
        assert db.scalars(select(Run)).one().flagged is False
        assert progress.snapshot(db, ids[1]).runs == 1
        assert [(a.kind, a.key) for a in progress.awards_of(db, ids[1])] == [("badge", "any_run")]
        assert any(a.action == "score_flag.cleared" for a in db.scalars(select(Activity)))


def test_clearing_a_worse_run_leaves_the_players_better_score_alone(env, monkeypatch):
    Session, client, tokens, ids = env
    flag_id = flagged_game(env, monkeypatch)
    with Session() as db:
        db.get(ScoreFlag, flag_id).stats = {"score": 50}  # pretend the flagged run was lower than their saved best
        db.commit()
    client.post(f"/admin/score-flags/{flag_id}/clear", json={}, headers=tokens[ids[10]])
    assert client.get("/games/lanedash/leaderboard").json()[0]["score"] == 100


def test_confirming_a_flag_keeps_the_run_out_for_good(env, monkeypatch):
    Session, client, tokens, ids = env
    flag_id = flagged_game(env, monkeypatch)
    res = client.post(f"/admin/score-flags/{flag_id}/confirm", json={"note": "impossible"}, headers=tokens[ids[10]])
    assert res.status_code == 200 and res.json()["status"] == "confirmed"
    assert client.get("/games/lanedash/leaderboard").json()[0]["score"] == 100
    with Session() as db:
        assert db.scalars(select(Run)).one().flagged is True
        assert any(a.action == "score_flag.confirmed" for a in db.scalars(select(Activity)))


def test_a_flag_can_only_be_decided_once(env, monkeypatch):
    _, client, tokens, ids = env
    flag_id = flagged_game(env, monkeypatch)
    assert client.post(f"/admin/score-flags/{flag_id}/clear", json={}, headers=tokens[ids[10]]).status_code == 200
    assert client.post(f"/admin/score-flags/{flag_id}/confirm", json={}, headers=tokens[ids[10]]).status_code == 409
    assert client.post("/admin/score-flags/99999/clear", json={}, headers=tokens[ids[10]]).status_code == 404


# --- the progress side, directly ---------------------------------------------------------------------------------

def test_a_flagged_run_is_logged_without_touching_the_players_day(env):
    Session, _, _, ids = env
    with Session() as db:
        out = progress.after_run(db, ids[1], "lanedash", {"score": 5}, flagged=True)
        assert out.unlocked == [] and db.scalar(select(func.count()).select_from(ActivityDay)) == 0
        assert db.get(Run, out.run_id).flagged is True


def test_releasing_a_run_counts_it_and_evaluates_the_rules(env):
    Session, _, _, ids = env
    progress.register("profiles", [progress.Rule("badge", "any_run", lambda s, r: s.runs >= 1)])
    with Session() as db:
        run_id = progress.after_run(db, ids[1], "lanedash", {"score": 5}, flagged=True).run_id
        unlocked = progress.release_run(db, run_id)
        assert unlocked == [{"kind": "badge", "key": "any_run"}]
        assert progress.snapshot(db, ids[1]).runs == 1
