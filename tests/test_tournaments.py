from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.db import Base, get_db
from app.main import app
from app.models import Game, Score, Tournament, TournamentScore, User
from app.security import create_game_session, create_token

# 2026-10-20 12:00 in Nigeria (UTC+1): today's tournament started yesterday at 18:00 and ends today at 18:00.
NOON = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 19, 17, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
PLAYERS = ("ada", "tunde", "chioma", "musa", "kofi")


class Clock:
    def __init__(self, at: datetime):
        self.at = at


@pytest.fixture
def clock(monkeypatch):
    c = Clock(NOON)
    monkeypatch.setattr(tournaments, "now", lambda: c.at)
    return c


@pytest.fixture
def env(clock):
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
        users = [User(email=f"{n}@example.com", name=n.title()) for n in PLAYERS]
        users[0].character = {"name": "Runner Ada", "style": {"primaryColor": "#a55eea"}}
        db.add_all(users)
        db.flush()
        # Exactly five games qualify, so every tournament uses all of them.
        db.add_all([Game(name=f"g{i}", owner_id=users[0].id, repo_url=f"https://github.com/ada/g{i}", leaderboard=BOARD) for i in range(5)])
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def enter(client, tokens, who):
    res = client.post("/tournaments/current/enter", headers=tokens[who])
    assert res.status_code == 200, res.text
    return res.json()


def play(client, tokens, who, game, score):
    res = client.post(f"/games/{game}/scores", json={"score": score}, headers=tokens[who])
    assert res.status_code == 200, res.text


# --- the day's window ---------------------------------------------------------------------


def test_the_window_runs_from_evening_to_evening_nigeria_time():
    assert tournaments.window(NOON) == (START, END)
    assert tournaments.window(END - timedelta(seconds=1)) == (START, END)
    assert tournaments.window(END) == (END, END + timedelta(days=1))  # 18:00 sharp: the next one starts


def test_the_week_starts_on_monday_nigeria_time():
    assert tournaments.week_start(NOON) == datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)  # Mon 19 Oct 00:00 WAT


# --- which games ----------------------------------------------------------------------------


def test_eligible_games(env):
    _, _, ids, Session = env
    with Session() as db:
        owner = ids["ada"]
        db.add_all([
            Game(name="hangout", owner_id=owner, repo_url="x", leaderboard=BOARD),  # start.ng's own space
            Game(name="fails", owner_id=owner, repo_url="x", leaderboard=BOARD, meets_standards=False),
            Game(name="meets", owner_id=owner, repo_url="x", leaderboard=BOARD, meets_standards=True),
            Game(name="no-board", owner_id=owner, repo_url="x"),  # no startng.json and no scores yet
            Game(name="plain-scores", owner_id=owner, repo_url="x"),
        ])
        db.add(Score(user_id=owner, game_name="plain-scores", best=5))
        db.commit()
        assert tournaments.eligible_games(db) == ["g0", "g1", "g2", "g3", "g4", "meets", "plain-scores"]


def test_the_days_tournament_is_made_once_automatically(env):
    client, _, _, Session = env
    first = client.get("/tournaments/current").json()
    assert first["starts_at"].startswith("2026-10-19T17:00:00")
    assert first["ends_at"].startswith("2026-10-20T17:00:00")
    assert sorted(g["name"] for g in first["games"]) == ["g0", "g1", "g2", "g3", "g4"]
    assert (first["entered"], first["entrants"], first["finished"]) == (False, 0, False)
    assert client.get("/tournaments/current").json()["id"] == first["id"]
    with Session() as db:
        assert db.scalar(select(Tournament).where(Tournament.id == first["id"])) is not None
        assert len(db.scalars(select(Tournament)).all()) == 1


def test_no_tournament_with_fewer_than_three_games(env):
    client, tokens, _, Session = env
    with Session() as db:
        for name in ("g0", "g1", "g2"):
            db.delete(db.get(Game, name))
        db.commit()
    assert client.get("/tournaments/current").json() is None
    assert client.post("/tournaments/current/enter", headers=tokens["ada"]).status_code == 404


def test_three_or_four_games_still_make_a_tournament(env):
    client, _, _, Session = env
    with Session() as db:
        db.delete(db.get(Game, "g0"))
        db.delete(db.get(Game, "g1"))
        db.commit()
    assert len(client.get("/tournaments/current").json()["games"]) == 3


def test_games_from_the_last_tournaments_wait_their_turn(env, clock):
    client, _, ids, Session = env
    with Session() as db:
        db.add_all([Game(name=f"h{i}", owner_id=ids["ada"], repo_url="x", leaderboard=BOARD) for i in range(5)])
        db.commit()
    today = {g["name"] for g in client.get("/tournaments/current").json()["games"]}
    clock.at = NOON + timedelta(days=1)
    tomorrow = {g["name"] for g in client.get("/tournaments/current").json()["games"]}
    assert len(today) == len(tomorrow) == 5
    assert not today & tomorrow


# --- entering and playing ---------------------------------------------------------------------


def test_entering_needs_a_start_ng_sign_in(env):
    client, _, ids, _ = env
    assert client.post("/tournaments/current/enter").status_code == 401
    session = {"Authorization": f"Bearer {create_game_session(ids['ada'], 'g0')}"}
    assert client.post("/tournaments/current/enter", headers=session).status_code == 401  # games can't enter players


def test_entering_twice_is_fine(env):
    client, tokens, _, _ = env
    enter(client, tokens, "ada")
    out = enter(client, tokens, "ada")
    assert (out["entered"], out["entrants"]) == (True, 1)
    assert client.get("/tournaments/current", headers=tokens["ada"]).json()["entered"] is True
    assert client.get("/tournaments/current", headers=tokens["tunde"]).json()["entered"] is False


def test_runs_count_only_after_entering_and_only_the_best(env):
    client, tokens, ids, _ = env
    client.get("/tournaments/current")
    play(client, tokens, "ada", "g0", 900)  # before entering: only the game's own leaderboard
    enter(client, tokens, "ada")
    play(client, tokens, "ada", "g0", 50)
    play(client, tokens, "ada", "g0", 80)
    play(client, tokens, "ada", "g0", 60)
    g0 = next(g for g in client.get("/tournaments/current").json()["games"] if g["name"] == "g0")
    assert g0["players"] == 1
    assert g0["top"][0]["stats"] == {"score": 80} and g0["top"][0]["points"] == 10
    assert client.get("/games/g0/leaderboard").json()[0]["score"] == 900  # the all-time board is unchanged


def test_runs_on_other_games_dont_count(env):
    client, tokens, ids, Session = env
    with Session() as db:
        db.add(Game(name="other", owner_id=ids["ada"], repo_url="x", leaderboard=BOARD, meets_standards=False))
        db.commit()
    enter(client, tokens, "ada")
    play(client, tokens, "ada", "other", 100)
    with Session() as db:
        assert db.scalars(select(TournamentScore)).all() == []


def test_live_standings(env):
    client, tokens, _, _ = env
    for who in ("ada", "tunde"):
        enter(client, tokens, who)
    play(client, tokens, "ada", "g0", 10)
    play(client, tokens, "tunde", "g0", 20)
    play(client, tokens, "ada", "g1", 5)
    out = client.get("/tournaments/current", headers=tokens["ada"]).json()
    assert [(s["name"], s["points"], s["firsts"]) for s in out["standings"]] == [("Runner Ada", 17, 1), ("Tunde", 10, 1)]
    assert out["me"]["place"] == 1 and out["winner"] is None


def test_an_owner_removing_a_score_removes_it_from_the_tournament(env):
    client, tokens, ids, Session = env
    enter(client, tokens, "tunde")
    play(client, tokens, "tunde", "g0", 999)
    assert client.delete(f"/games/g0/scores/{ids['tunde']}", headers=tokens["ada"]).status_code == 204
    with Session() as db:
        assert db.scalars(select(TournamentScore)).all() == []


# --- results, points and the overall leaderboard -------------------------------------------------


def test_points_winner_and_overall_leaderboard(env, clock):
    client, tokens, _, _ = env
    for who in PLAYERS:
        enter(client, tokens, who)
    for who, score in zip(PLAYERS, (50, 40, 30, 20, 10)):  # g0: 10, 7, 5, 3, 1
        play(client, tokens, who, "g0", score)
    play(client, tokens, "kofi", "g1", 100)  # kofi: 1 + 10 = 11
    assert client.get("/tournaments/recent").json() == []

    clock.at = END + timedelta(minutes=5)  # 18:05: the day is over
    assert client.get("/tournaments/current").json()["starts_at"].startswith("2026-10-20T17:00:00")
    past = client.get("/tournaments/recent").json()
    assert len(past) == 1
    assert past[0]["winner"]["name"] == "Kofi" and past[0]["winner"]["points"] == 11
    assert [p["name"] for p in past[0]["podium"]] == ["Kofi", "Runner Ada", "Tunde"]

    board = client.get("/tournaments/leaderboard").json()
    assert [(r["name"], r["points"], r["wins"], r["firsts"]) for r in board] == [
        ("Kofi", 11, 1, 1), ("Runner Ada", 10, 0, 1), ("Tunde", 7, 0, 0), ("Chioma", 5, 0, 0), ("Musa", 3, 0, 0),
    ]
    assert client.get("/tournaments/leaderboard?period=week").json() == board
    assert client.get("/tournaments/leaderboard?period=month").status_code == 422


def test_settling_is_done_once(env, clock):
    from app import events
    finished = events.subscribe("tournament_finished")(lambda p: got.append(p))
    got = []
    client, tokens, ids, Session = env
    enter(client, tokens, "ada")
    play(client, tokens, "ada", "g0", 5)
    clock.at = END + timedelta(hours=1)
    try:
        client.get("/tournaments/recent")
        client.get("/tournaments/leaderboard")
        with Session() as db:
            t = db.scalars(select(Tournament)).first()
            tournaments.settle(db, t, clock.at)  # already settled: nothing changes
    finally:
        events.unsubscribe("tournament_finished", finished)
    assert client.get("/tournaments/leaderboard").json()[0]["points"] == 10
    assert len(got) == 1 and got[0]["winner_id"] == ids["ada"] and got[0]["players"] == 1  # the event fires once


def test_ties_go_to_more_first_places_then_to_who_got_there_first(env, clock):
    client, tokens, _, _ = env
    for who in ("ada", "tunde", "chioma"):
        enter(client, tokens, who)
    play(client, tokens, "ada", "g0", 9)      # ada 10
    play(client, tokens, "tunde", "g1", 9)    # tunde 10, later than ada
    clock.at = END + timedelta(minutes=1)
    podium = client.get("/tournaments/recent").json()[0]["podium"]
    assert [p["name"] for p in podium] == ["Runner Ada", "Tunde"]  # same points and firsts: ada got there first


def test_this_week_leaves_out_last_weeks_tournaments(env, clock):
    client, tokens, _, _ = env
    clock.at = NOON - timedelta(days=7)  # last week's Tuesday
    enter(client, tokens, "ada")
    play(client, tokens, "ada", "g0", 5)
    clock.at = NOON
    enter(client, tokens, "tunde")
    play(client, tokens, "tunde", "g0", 5)
    clock.at = END + timedelta(minutes=1)
    assert [r["name"] for r in client.get("/tournaments/leaderboard").json()] == ["Runner Ada", "Tunde"]
    assert [r["name"] for r in client.get("/tournaments/leaderboard?period=week").json()] == ["Tunde"]


def test_publishing_records_whether_a_game_meets_the_standards(tmp_path, monkeypatch):
    from app.game_store import LocalGameStore, get_game_store
    from app.routers import games as games_router
    from tests.test_publish import GAME, make_zip

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    app.dependency_overrides[get_db] = lambda: (yield Session())
    app.dependency_overrides[get_game_store] = lambda: LocalGameStore(tmp_path)
    archives = {"zip": make_zip(GAME)}
    monkeypatch.setattr(games_router, "download_repo", lambda repo: archives["zip"])
    with Session() as db:
        db.add(User(email="ada@example.com", name="Ada"))
        db.commit()
        token = {"Authorization": f"Bearer {create_token(1)}"}
    client = TestClient(app)
    try:
        assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/plain", "name": "plain"}, headers=token).status_code == 200
        meets = {**GAME, "end.js": 'location.href="https://start.ng"; const t="Play more on start.ng"; open("https://twitter.com/intent/tweet?text=hi")'}
        archives["zip"] = make_zip(meets)
        assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/good", "name": "good"}, headers=token).status_code == 200
        with Session() as db:
            assert db.get(Game, "plain").meets_standards is False
            assert db.get(Game, "good").meets_standards is True
    finally:
        app.dependency_overrides.clear()
