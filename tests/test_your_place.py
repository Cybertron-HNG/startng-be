"""GET /games/{name}/leaderboard/me: the signed-in player's rank and the players just above them."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Game, Score, User
from app.security import create_token


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
        users = [User(email=f"p{n}@example.com", name=f"P{n}") for n in range(6)]
        db.add_all(users)
        db.flush()
        db.add(Game(name="dash", owner_id=users[0].id, repo_url="https://github.com/a/dash"))
        for n, u in enumerate(users[:5]):  # P0 has the best score, P4 the worst; P5 has none
            db.add(Score(user_id=u.id, game_name="dash", best=100 - n * 10))
        db.commit()
        tokens = [{"Authorization": f"Bearer {create_token(u.id)}"} for u in users]
    yield TestClient(app), tokens
    app.dependency_overrides.clear()


def test_shows_rank_and_the_two_players_above(env):
    client, tokens = env
    body = client.get("/games/dash/leaderboard/me", headers=tokens[3]).json()
    assert body["rank"] == 4 and body["total"] == 5
    assert body["you"]["score"] == 70
    assert [e["rank"] for e in body["above"]] == [2, 3]


def test_the_leader_has_nobody_above(env):
    client, tokens = env
    body = client.get("/games/dash/leaderboard/me", headers=tokens[0]).json()
    assert body["rank"] == 1 and body["above"] == []


def test_a_player_without_a_score_gets_no_rank(env):
    client, tokens = env
    body = client.get("/games/dash/leaderboard/me", headers=tokens[5]).json()
    assert body == {"rank": None, "you": None, "above": [], "total": 5}


def test_needs_a_sign_in_and_a_real_game(env):
    client, tokens = env
    assert client.get("/games/dash/leaderboard/me").status_code == 401
    assert client.get("/games/nope/leaderboard/me", headers=tokens[0]).status_code == 404
