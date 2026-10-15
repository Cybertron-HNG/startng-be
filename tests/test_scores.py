import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Game, User
from app.security import create_token

STYLE = {"primaryColor": "#a55eea"}


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
        # Five start.ng users; Ada has a character, the others only a name.
        users = [User(email=f"{n}@example.com", name=n.title()) for n in ("ada", "tunde", "chioma", "musa", "kofi")]
        users[0].character = {"name": "Runner Ada", "style": STYLE}
        db.add_all(users)
        db.flush()
        db.add(Game(name="lanedash", owner_id=users[0].id, repo_url="https://github.com/ada/lanedash"))
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids
    app.dependency_overrides.clear()


def post_score(client, tokens, who, score, game="lanedash"):
    return client.post(f"/games/{game}/scores", json={"score": score}, headers=tokens[who])


def test_needs_sign_in(env):
    client, _, _ = env
    assert client.post("/games/lanedash/scores", json={"score": 10}).status_code == 401


def test_unknown_game(env):
    client, tokens, _ = env
    assert post_score(client, tokens, "ada", 10, game="nope").status_code == 404
    assert client.get("/games/nope/leaderboard").status_code == 404


@pytest.mark.parametrize("score", [-1, 1_000_000_001, "lots"])
def test_refuses_silly_scores(env, score):
    client, tokens, _ = env
    assert post_score(client, tokens, "ada", score).status_code == 422


def test_keeps_each_players_best(env):
    client, tokens, _ = env
    assert post_score(client, tokens, "ada", 100).json() == {"best": 100, "improved": True, "rank": 1, "stats": {"score": 100}}
    assert post_score(client, tokens, "ada", 50).json() == {"best": 100, "improved": False, "rank": 1, "stats": {"score": 100}}
    assert post_score(client, tokens, "ada", 150).json() == {"best": 150, "improved": True, "rank": 1, "stats": {"score": 150}}
    assert [e["score"] for e in client.get("/games/lanedash/leaderboard").json()] == [150]


def test_three_of_five_users_play(env):
    client, tokens, ids = env
    post_score(client, tokens, "tunde", 300)
    post_score(client, tokens, "ada", 120)
    post_score(client, tokens, "chioma", 450)
    assert post_score(client, tokens, "ada", 200).json()["rank"] == 3

    board = client.get("/games/lanedash/leaderboard").json()
    assert [(e["rank"], e["name"], e["score"]) for e in board] == [
        (1, "Chioma", 450),
        (2, "Tunde", 300),
        (3, "Runner Ada", 200),
    ]
    assert board[2]["player_id"] == ids["ada"]
    assert board[2]["color"] == "#a55eea"
    assert board[0]["color"] is None
    # Musa and Kofi never played, so they aren't on it.
    assert {e["name"] for e in board}.isdisjoint({"Musa", "Kofi"})


def test_limit_and_ties(env):
    client, tokens, _ = env
    post_score(client, tokens, "musa", 500)
    post_score(client, tokens, "kofi", 500)  # same score, later: ranked after Musa
    post_score(client, tokens, "tunde", 10)
    board = client.get("/games/lanedash/leaderboard?limit=2").json()
    assert [e["name"] for e in board] == ["Musa", "Kofi"]
    assert client.get("/games/lanedash/leaderboard?limit=0").status_code == 422
