"""F9: admins make private tournaments to share in a group. Only people who register see the games; it can start
later; the admin isn't a member."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.config import settings
from app.db import Base, get_db
from app.features.private_tournaments import service
from app.main import app
from app.models import Game, User
from app.security import create_token

BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)


class Clock:
    at = NOW


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(tournaments, "now", lambda: clock.at)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(settings, "features", "private_tournaments")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "site_url", "https://start.ng")
    monkeypatch.setattr(service, "SessionLocal", Session)  # score events open their own session
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada", "tunde", "kofi")}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name=f"g{i}", owner_id=users["ada"].id, repo_url=f"https://github.com/ada/g{i}", leaderboard=BOARD)
                    for i in range(3)])
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, clock
    app.dependency_overrides.clear()


def create(client, tokens, who="boss", start=timedelta(days=1), length=timedelta(hours=3), **extra):
    body = {"title": "Lagos Game Night", "games": ["g0", "g1"], "starts_at": (NOW + start).isoformat(),
            "ends_at": (NOW + start + length).isoformat(), **extra}
    return client.post("/admin/private-tournaments", json=body, headers=tokens[who])


def play(client, tokens, who, game, score):
    assert client.post(f"/games/{game}/scores", json={"score": score}, headers=tokens[who]).status_code == 200


def test_only_admins_make_them(env):
    client, tokens, clock = env
    assert create(client, tokens, who="ada").status_code == 404
    res = create(client, tokens)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["by_admin"] and body["members"] == 0 and body["game_names"] == ["g0", "g1"]
    assert body["link"] == f"https://start.ng/friends/{body['code']}"
    assert [t["code"] for t in client.get("/admin/private-tournaments", headers=tokens["boss"]).json()] == [body["code"]]


def test_register_to_see_the_games_and_runs_count_in_the_window(env):
    client, tokens, clock = env
    code = create(client, tokens).json()["code"]
    seen = client.get(f"/private-tournaments/{code}").json()  # anyone with the link, signed out
    assert (seen["title"], seen["games"], seen["by_admin"]) == ("Lagos Game Night", [], True)
    assert "g0" not in client.get(f"/private-tournaments/{code}/page").text  # nor in the link preview
    assert client.get(f"/private-tournaments/{code}", headers=tokens["ada"]).json()["games"] == []
    reg = client.post(f"/private-tournaments/{code}/join", headers=tokens["ada"]).json()
    assert reg["joined"] and reg["games"] == ["g0", "g1"]
    play(client, tokens, "ada", "g0", 900)  # before it starts: doesn't count
    clock.at = NOW + timedelta(days=1, hours=1)
    play(client, tokens, "ada", "g0", 50)
    t = client.get(f"/private-tournaments/{code}", headers=tokens["ada"]).json()
    assert [(s["name"], s["points"]) for s in t["standings"]] == [("Ada", 10)]
    assert t["game_boards"][0]["top"][0]["stats"]["score"] == 50
    assert "Boss" not in [s["name"] for s in t["standings"]]  # the admin who made it isn't a member


@pytest.mark.parametrize("start,length,extra,code", [
    (-timedelta(hours=1), timedelta(hours=3), {}, "starts_at"),
    (timedelta(hours=1), timedelta(minutes=30), {}, "duration"),
    (timedelta(hours=1), timedelta(days=31), {}, "duration"),
    (timedelta(hours=1), timedelta(hours=3), {"max_members": 1}, "max_members"),
])
def test_the_rules(env, start, length, extra, code):
    client, tokens, clock = env
    res = create(client, tokens, start=start, length=length, **extra)
    assert res.status_code == 400 and res.json()["errors"][0]["code"] == code


def test_a_player_limit_new_link_and_delete(env):
    client, tokens, clock = env
    code = create(client, tokens, max_members=2).json()["code"]
    for who in ("ada", "tunde"):
        assert client.post(f"/private-tournaments/{code}/join", headers=tokens[who]).status_code == 200
    full = client.post(f"/private-tournaments/{code}/join", headers=tokens["kofi"])
    assert full.status_code == 400 and full.json()["errors"][0]["code"] == "full"
    new = client.post(f"/admin/private-tournaments/{code}/new-link", headers=tokens["boss"]).json()["code"]
    assert new != code and client.get(f"/private-tournaments/{code}").status_code == 404
    assert client.get(f"/private-tournaments/{new}", headers=tokens["ada"]).json()["joined"]  # players stay in
    assert client.delete(f"/admin/private-tournaments/{new}", headers=tokens["boss"]).status_code == 204
    assert client.get(f"/private-tournaments/{new}").status_code == 404
    # a tournament a player made isn't the admin page's to change
    mine = client.post("/private-tournaments", json={"title": "Family Cup", "games": ["g0"], "hours": 48}, headers=tokens["ada"]).json()
    assert client.delete(f"/admin/private-tournaments/{mine['code']}", headers=tokens["boss"]).status_code == 404
    assert client.get(f"/private-tournaments/{mine['code']}").json()["games"] == ["g0"]  # games shown as before
