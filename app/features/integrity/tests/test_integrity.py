import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import leaderboard as boards
from app.config import settings
from app.db import Base, get_db
from app.features.integrity import service as integrity
from app.main import app
from app.models import Game, Score, User
from app.routers.games import _ranked as old_ranked
from app.security import create_token

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
ARENA = [
    {"key": "stage", "label": "Stage", "order": "highest", "format": "number"},
    {"key": "kills", "label": "Kills", "order": "highest", "format": "number"},
    {"key": "time", "label": "Time", "order": "lowest", "format": "time"},
]
FASTEST = [{"key": "time", "label": "Time", "order": "lowest", "format": "time"}, {"key": "score", "label": "Score", "order": "highest", "format": "number"}]


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
        users = [User(email=f"p{n}@example.com", name=f"P{n}") for n in range(40)]
        db.add_all(users)
        db.flush()
        owner = users[0].id
        db.add_all([
            Game(name="lanedash", owner_id=owner, repo_url="https://github.com/a/lanedash"),
            Game(name="arena", owner_id=owner, repo_url="https://github.com/a/arena", leaderboard=ARENA),
            Game(name="speedrun", owner_id=owner, repo_url="https://github.com/a/speedrun", leaderboard=FASTEST),
            Game(name="capped", owner_id=owner, repo_url="https://github.com/a/capped",
                 leaderboard=[{"key": "score", "label": "Score", "order": "highest", "format": "number", "max": 5000}]),
            Game(name="capped2", owner_id=owner, repo_url="https://github.com/a/capped2",
                 leaderboard=[{"key": "stage", "label": "Stage", "order": "highest", "format": "number", "max": 10},
                              {"key": "kills", "label": "Kills", "order": "highest", "format": "number", "max": 200}]),
        ])
        db.commit()
        ids = [u.id for u in users]
        tokens = {u.id: {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
    yield Session, TestClient(app), tokens, ids
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fresh_state():
    integrity.reset()
    yield
    integrity.reset()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "features", "integrity")


def schema_of(db, name):
    return db.get(Game, name).leaderboard or boards.DEFAULT


def seed(db, ids, game, schema, seed_value, legacy=False):
    """Scores with plenty of ties, so tie-breaking is exercised."""
    rng = random.Random(seed_value)
    for n, uid in enumerate(ids):
        if legacy and n % 7 == 0:
            db.add(Score(user_id=uid, game_name=game, best=rng.randint(1, 3), stats=None, updated_at=BASE + timedelta(seconds=n)))
            continue
        if legacy and n % 11 == 0:  # a row from before the schema changed: the first stat is missing
            stats = {k["key"]: rng.randint(1, 3) for k in schema[1:]}
            db.add(Score(user_id=uid, game_name=game, best=rng.randint(1, 3), stats=stats, updated_at=BASE + timedelta(seconds=n)))
            continue
        stats = {s["key"]: rng.randint(1, 4) for s in schema}
        db.add(Score(user_id=uid, game_name=game, best=stats[schema[0]["key"]], stats=stats, updated_at=BASE + timedelta(seconds=rng.randint(0, 5))))
    db.commit()


# --- limits in startng.json --------------------------------------------------------------------------------------

def test_the_manifest_accepts_a_max_per_stat():
    out = boards.parse_leaderboard({"stats": [{"key": "score", "label": "Score", "max": 5000}]})
    assert out[0]["max"] == 5000


def test_a_stat_without_a_max_looks_exactly_as_before():
    assert boards.parse_leaderboard({"stats": [{"key": "score", "label": "Score"}]}) == [
        {"key": "score", "label": "Score", "order": "highest", "format": "number"}]


@pytest.mark.parametrize("bad", [0, -5, "9", True, 1.5, 1_000_000_001])
def test_a_bad_max_is_refused_with_a_clear_message(bad):
    with pytest.raises(boards.SchemaError, match="max"):
        boards.parse_leaderboard({"stats": [{"key": "score", "label": "Score", "max": bad}]})


def post(client, tokens, uid, game, **body):
    return client.post(f"/games/{game}/scores", json=body, headers=tokens[uid])


def test_a_score_above_the_max_is_refused(env, on):
    _, client, tokens, ids = env
    res = post(client, tokens, ids[1], "capped", score=5001)
    assert res.status_code == 422
    body = res.json()
    assert body["ok"] is False
    assert body["errors"][0]["code"] == "score_out_of_range"
    assert "5,000" in body["detail"] or "5000" in body["detail"]


def test_a_score_at_the_max_is_accepted(env, on):
    _, client, tokens, ids = env
    assert post(client, tokens, ids[1], "capped", score=5000).status_code == 200


def test_refused_scores_are_not_saved(env, on):
    Session, client, tokens, ids = env
    post(client, tokens, ids[1], "capped", score=9999)
    with Session() as db:
        assert db.query(Score).count() == 0


def test_every_stat_is_checked_against_its_own_max(env, on):
    _, client, tokens, ids = env
    assert post(client, tokens, ids[1], "capped2", stats={"stage": 10, "kills": 200}).status_code == 200
    res = post(client, tokens, ids[2], "capped2", stats={"stage": 3, "kills": 201})
    assert res.status_code == 422 and res.json()["errors"][0]["code"] == "score_out_of_range"
    assert "kills" in res.json()["detail"]


def test_limits_are_ignored_while_the_feature_is_off(env):
    _, client, tokens, ids = env
    assert post(client, tokens, ids[1], "capped", score=999_999).status_code == 200


# --- rate limit --------------------------------------------------------------------------------------------------

@pytest.fixture
def limited(monkeypatch, on):
    monkeypatch.setattr(settings, "scores_per_minute", 3)
    now = [1000.0]
    monkeypatch.setattr(integrity, "clock", lambda: now[0])
    return now


def test_too_many_scores_in_a_minute_are_refused(env, limited):
    _, client, tokens, ids = env
    for n in range(3):
        assert post(client, tokens, ids[1], "lanedash", score=n).status_code == 200
    res = post(client, tokens, ids[1], "lanedash", score=9)
    assert res.status_code == 429
    assert res.json()["errors"][0]["code"] == "rate_limited"
    assert int(res.headers["retry-after"]) >= 1


def test_the_limit_is_per_player_and_per_game(env, limited):
    _, client, tokens, ids = env
    for n in range(3):
        post(client, tokens, ids[1], "lanedash", score=n)
    assert post(client, tokens, ids[2], "lanedash", score=1).status_code == 200  # another player
    assert post(client, tokens, ids[1], "capped", score=1).status_code == 200  # another game


def test_the_window_slides(env, limited):
    _, client, tokens, ids = env
    for n in range(3):
        post(client, tokens, ids[1], "lanedash", score=n)
    assert post(client, tokens, ids[1], "lanedash", score=9).status_code == 429
    limited[0] += 61
    assert post(client, tokens, ids[1], "lanedash", score=9).status_code == 200


def test_there_is_no_rate_limit_while_the_feature_is_off(env, monkeypatch):
    _, client, tokens, ids = env
    monkeypatch.setattr(settings, "scores_per_minute", 1)
    for n in range(5):
        assert post(client, tokens, ids[1], "lanedash", score=n).status_code == 200


# --- the ranking layer matches today's ranking -------------------------------------------------------------------

@pytest.mark.parametrize("game,schema,legacy", [
    ("arena", ARENA, False), ("arena", ARENA, True), ("speedrun", FASTEST, False), ("lanedash", boards.DEFAULT, True)])
@pytest.mark.parametrize("seed_value", [1, 2, 3])
def test_ranked_matches_the_current_sort_in_the_same_order(env, game, schema, legacy, seed_value):
    Session, _, _, ids = env
    with Session() as db:
        seed(db, ids, game, schema, seed_value, legacy)
        expected = [r.user_id for r in old_ranked(db, game, schema_of(db, game))]
        for limit in (1, 5, 10, 50, 100):
            got = [r.user_id for r in integrity.ranked(db, game, limit)]
            assert got == expected[:limit], (game, seed_value, limit)


@pytest.mark.parametrize("game,schema", [("arena", ARENA), ("speedrun", FASTEST)])
def test_rank_of_matches_the_current_sort(env, game, schema):
    Session, _, _, ids = env
    with Session() as db:
        seed(db, ids, game, schema, 7, legacy=True)
        order = [r.user_id for r in old_ranked(db, game, schema_of(db, game))]
        for uid in ids:
            want = order.index(uid) + 1 if uid in order else None
            assert integrity.rank_of(db, uid, game) == want


def test_rank_of_is_none_for_a_player_with_no_score(env):
    Session, _, _, ids = env
    with Session() as db:
        assert integrity.rank_of(db, ids[1], "arena") is None


def test_only_users_ranks_just_those_players(env):
    Session, _, _, ids = env
    with Session() as db:
        seed(db, ids, "arena", ARENA, 5)
        everyone = [r.user_id for r in old_ranked(db, "arena", ARENA)]
        subset = set(ids[3:12])
        want = [uid for uid in everyone if uid in subset]
        assert [r.user_id for r in integrity.ranked(db, "arena", 50, only_users=subset)] == want
        assert [r.user_id for r in integrity.ranked(db, "arena", 3, only_users=subset)] == want[:3]


def test_a_tie_goes_to_whoever_got_there_first(env):
    Session, _, _, ids = env
    with Session() as db:
        stats = {"stage": 3, "kills": 5, "time": 20}
        for n, uid in enumerate(ids[1:4]):
            db.add(Score(user_id=uid, game_name="arena", best=3, stats=stats, updated_at=BASE + timedelta(minutes=3 - n)))
        db.commit()
        assert [r.user_id for r in integrity.ranked(db, "arena", 10)] == [ids[3], ids[2], ids[1]]


# --- the routes with the feature on ------------------------------------------------------------------------------

def fill_via_db(Session, ids, game="arena", schema=ARENA, seed_value=11):
    with Session() as db:
        seed(db, ids, game, schema, seed_value)


def test_the_leaderboard_response_is_identical_with_the_feature_on_and_off(env, monkeypatch):
    Session, client, _, ids = env
    fill_via_db(Session, ids)
    monkeypatch.setattr(settings, "features", "")
    off = client.get("/games/arena/leaderboard?limit=15")
    monkeypatch.setattr(settings, "features", "integrity")
    on_ = client.get("/games/arena/leaderboard?limit=15")
    assert off.status_code == on_.status_code == 200
    assert on_.content == off.content


def test_the_submit_rank_matches_with_the_feature_on_and_off(env, monkeypatch):
    Session, client, tokens, ids = env
    fill_via_db(Session, ids[1:30])
    body = {"stats": {"stage": 3, "kills": 2, "time": 12}}
    monkeypatch.setattr(settings, "features", "")
    off = client.post("/games/arena/scores", json=body, headers=tokens[ids[35]]).json()
    # remove that score again and repeat it with the feature on
    with Session() as db:
        db.query(Score).filter(Score.user_id == ids[35]).delete()
        db.commit()
    monkeypatch.setattr(settings, "features", "integrity")
    on_ = client.post("/games/arena/scores", json=body, headers=tokens[ids[35]]).json()
    assert on_["rank"] == off["rank"] and on_["best"] == off["best"] and on_["stats"] == off["stats"]


# --- the 30 second cache -----------------------------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(integrity, "clock", lambda: now[0])
    return now


def test_a_leaderboard_is_served_from_the_cache_for_30_seconds(env, on, clock):
    Session, client, _, ids = env
    fill_via_db(Session, ids[:10])
    first = client.get("/games/arena/leaderboard").json()
    with Session() as db:  # a score that bypasses the route, so nothing invalidates the cache
        db.add(Score(user_id=ids[20], game_name="arena", best=99, stats={"stage": 99, "kills": 99, "time": 1}, updated_at=BASE))
        db.commit()
    assert client.get("/games/arena/leaderboard").json() == first
    clock[0] += 31
    assert client.get("/games/arena/leaderboard").json() != first


def test_a_new_score_clears_that_games_cache_at_once(env, on, clock):
    Session, client, tokens, ids = env
    fill_via_db(Session, ids[:10])
    before = client.get("/games/arena/leaderboard").json()
    post(client, tokens, ids[20], "arena", stats={"stage": 99, "kills": 99, "time": 1})
    after = client.get("/games/arena/leaderboard").json()
    assert after[0]["player_id"] == ids[20] and after != before


def test_removing_a_score_clears_the_cache_too(env, on, clock):
    Session, client, tokens, ids = env
    fill_via_db(Session, ids[:10])
    top = client.get("/games/arena/leaderboard").json()[0]["player_id"]
    assert client.delete(f"/games/arena/scores/{top}", headers=tokens[ids[0]]).status_code == 204
    assert client.get("/games/arena/leaderboard").json()[0]["player_id"] != top


def test_the_cache_keeps_games_and_limits_apart(env, on, clock):
    Session, client, _, ids = env
    fill_via_db(Session, ids[:10])
    fill_via_db(Session, ids[:10], "speedrun", FASTEST, 3)
    assert len(client.get("/games/arena/leaderboard?limit=3").json()) == 3
    assert len(client.get("/games/arena/leaderboard?limit=8").json()) == 8
    assert client.get("/games/speedrun/leaderboard?limit=3").json() != client.get("/games/arena/leaderboard?limit=3").json()


# --- which way the first stat is read ----------------------------------------------------------------------------

def key_for(game, schema):
    return (game, tuple((s["key"], s["order"]) for s in schema))


def test_the_indexed_column_is_used_when_it_agrees_with_every_row(env):
    Session, _, _, ids = env
    with Session() as db:
        seed(db, ids, "arena", ARENA, 1)
        integrity.ranked(db, "arena", 10)
        assert integrity._consistent[key_for("arena", ARENA)] is True


def test_the_stats_are_read_instead_when_a_row_disagrees_with_the_column(env):
    Session, _, _, ids = env
    with Session() as db:
        seed(db, ids, "arena", ARENA, 1, legacy=True)  # includes rows saved before the schema had these stats
        integrity.ranked(db, "arena", 10)
        assert integrity._consistent[key_for("arena", ARENA)] is False


# --- ranking a crowd of ties without loading them ----------------------------------------------------------------

def test_ranking_the_top_few_does_not_load_the_players_tied_with_them(env):
    """200 players share the best first stat. Asking for the top 5 must be answered by the database in one go, not by
    fetching every tied row to sort them here (that was slow on a big game)."""
    from sqlalchemy import event
    Session, _, _, ids = env
    with Session() as db:
        rng = random.Random(4)
        users = [User(email=f"tie{n}@example.com", name=f"T{n}") for n in range(200)]
        db.add_all(users)
        db.flush()
        for n, u in enumerate(users):
            stats = {"stage": 5, "kills": rng.randint(0, 9), "time": rng.randint(10, 30)}
            db.add(Score(user_id=u.id, game_name="arena", best=5, stats=stats, updated_at=BASE + timedelta(seconds=n % 7)))
        db.commit()
        integrity.ranked(db, "arena", 5)  # the first call also does the once-per-game checks
        statements, rows = [], []
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", lambda conn, cur, stmt, *a: statements.append(stmt))
        got = integrity.ranked(db, "arena", 5)
        assert len(got) == 5
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) <= 2, f"{len(selects)} queries: {selects}"  # the game, and the ranking itself
        expected = [r.user_id for r in old_ranked(db, "arena", ARENA)][:5]
        assert [r.user_id for r in got] == expected
