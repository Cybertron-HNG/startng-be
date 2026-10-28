from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import flags
from app.config import settings
from app.db import Base, get_db
from app.features import load
from app.models import Game, User
from app.security import create_token


def setup(monkeypatch):
    [feature] = load(["player_pulse"])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    app = FastAPI()
    app.include_router(feature.router, dependencies=[Depends(flags.require(feature.NAME))])
    app.dependency_overrides[get_db] = lambda: (yield Session()) or None
    with Session() as db:
        db.add_all([User(email="dan@example.com", name="Dan"), User(email="ada@example.com", name="Ada"),
                    User(email="tunde@example.com", name="Tunde")])
        db.flush()
        db.add(Game(name="coin-rush", owner_id=1, repo_url="https://github.com/dan/coin-rush"))
        db.commit()
    monkeypatch.setattr(settings, "features", feature.NAME)
    return TestClient(app), {n: {"Authorization": f"Bearer {create_token(i)}"} for i, n in [(1, "dan"), (2, "ada"), (3, "tunde")]}


def test_ships_dark(monkeypatch):
    client, h = setup(monkeypatch)
    monkeypatch.setattr(settings, "features", "")
    assert client.post("/games/coin-rush/pulse", json={"feel": "loved"}, headers=h["ada"]).status_code == 404


def test_players_answer_and_the_creator_sees_the_summary(monkeypatch):
    client, h = setup(monkeypatch)
    assert client.post("/games/coin-rush/pulse", json={"feel": "too_hard", "note": "level 3 is too fast"}, headers=h["ada"]).status_code == 201
    assert client.post("/games/coin-rush/pulse", json={"feel": "loved"}, headers=h["tunde"]).status_code == 201
    res = client.get("/games/coin-rush/pulse", headers=h["dan"])
    assert res.status_code == 200
    body = res.json()
    assert (body["answers"], body["loved"], body["too_hard"], body["too_easy"], body["loved_pct"]) == (2, 1, 1, 0, 50)
    assert [n["note"] for n in body["notes"]] == ["level 3 is too fast"]
    assert "user" not in str(body["notes"])  # players are never named


def test_a_second_answer_the_same_day_replaces_the_first(monkeypatch):
    client, h = setup(monkeypatch)
    client.post("/games/coin-rush/pulse", json={"feel": "too_hard"}, headers=h["ada"])
    client.post("/games/coin-rush/pulse", json={"feel": "loved", "note": "  "}, headers=h["ada"])
    body = client.get("/games/coin-rush/pulse", headers=h["dan"]).json()
    assert (body["answers"], body["loved"], body["too_hard"], body["notes"]) == (1, 1, 0, [])


def test_only_the_creator_sees_it_and_answers_need_sign_in(monkeypatch):
    client, h = setup(monkeypatch)
    assert client.get("/games/coin-rush/pulse", headers=h["ada"]).status_code == 403
    assert client.post("/games/coin-rush/pulse", json={"feel": "loved"}).status_code == 401
    assert client.post("/games/nope/pulse", json={"feel": "loved"}, headers=h["ada"]).status_code == 404
    assert client.post("/games/coin-rush/pulse", json={"feel": "meh"}, headers=h["ada"]).status_code == 422
    assert client.post("/games/coin-rush/pulse", json={"feel": "loved", "note": "x" * 141}, headers=h["ada"]).status_code == 422
