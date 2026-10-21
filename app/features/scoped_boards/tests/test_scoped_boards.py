from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import Game, Score, Tournament, TournamentPoints, User
from app.security import create_token

PLAYERS = ("ada", "tunde", "chioma", "musa")
BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(settings, "features", "*")
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in PLAYERS]
        db.add_all(users)
        db.flush()
        db.add(Game(name="g0", owner_id=users[0].id, repo_url="https://github.com/ada/g0", leaderboard=BOARD))
        db.flush()
        ago = tournaments.now() - timedelta(days=2)
        t = Tournament(starts_at=ago - timedelta(days=1), ends_at=ago, finished_at=ago)
        db.add(t)
        db.flush()
        for u, points, best in zip(users, (10, 5, 20, 7), (300, 500, 900, 100)):
            db.add(TournamentPoints(tournament_id=t.id, user_id=u.id, points=points, firsts=0, place=1))
            db.add(Score(user_id=u.id, game_name="g0", best=best, stats={"score": best}))
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
    client = TestClient(app)
    crew = client.post("/crews", json={"name": "Lagos Family", "kind": "family"}, headers=tokens["ada"]).json()
    client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"])
    yield client, tokens, crew
    app.dependency_overrides.clear()


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, crew = env
    monkeypatch.setattr(settings, "features", "crews")
    assert client.get("/boards/overall", params={"scope": f"crew:{crew['id']}"}, headers=tokens["ada"]).status_code == 404


def test_the_crews_overall_board_has_only_its_members(env):
    client, tokens, crew = env
    everyone = client.get("/tournaments/leaderboard").json()
    assert [r["name"] for r in everyone] == ["Chioma", "Ada", "Musa", "Tunde"]  # the main board is unchanged
    rows = client.get("/boards/overall", params={"scope": f"crew:{crew['id']}"}, headers=tokens["tunde"]).json()
    assert [(r["rank"], r["name"], r["points"]) for r in rows] == [(1, "Ada", 10), (2, "Tunde", 5)]


def test_a_game_board_for_the_crew_ranks_only_its_members(env):
    client, tokens, crew = env
    rows = client.get("/boards/game/g0", params={"scope": f"crew:{crew['id']}"}, headers=tokens["ada"]).json()
    assert [(r["rank"], r["name"], r["score"]) for r in rows] == [(1, "Tunde", 500), (2, "Ada", 300)]
    assert client.get("/boards/game/nope", params={"scope": f"crew:{crew['id']}"}, headers=tokens["ada"]).status_code == 404


def test_a_crew_board_is_invisible_to_non_members_and_signed_out(env):
    client, tokens, crew = env
    scope = {"scope": f"crew:{crew['id']}"}
    assert client.get("/boards/overall", params=scope, headers=tokens["chioma"]).status_code == 404
    assert client.get("/boards/game/g0", params=scope, headers=tokens["chioma"]).status_code == 404
    assert client.get("/boards/overall", params=scope).status_code == 401
    assert client.get("/boards/overall", params={"scope": "crew:9999"}, headers=tokens["ada"]).status_code == 404


@pytest.mark.parametrize("scope", ["friends", "crew:", "crew:abc", "all"])
def test_other_scopes_are_refused(env, scope):
    client, tokens, _ = env
    assert client.get("/boards/overall", params={"scope": scope}, headers=tokens["ada"]).status_code == 422

