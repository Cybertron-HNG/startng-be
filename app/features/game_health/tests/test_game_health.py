from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.game_health import service
from app.features.game_health.models import GameHealth
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import Game, User
from app.security import create_token

GOOD = b"<!doctype html><link rel='stylesheet' href='style.css'><canvas></canvas><script src='./js/game.js?v=2'></script>"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "game_health")
    monkeypatch.setattr(settings, "game_health_http", False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    store = LocalGameStore(tmp_path)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_game_store] = lambda: store
    monkeypatch.setattr(service, "SessionLocal", Session)
    games = {
        "lane-dash": {"index.html": GOOD, "style.css": b"body{}", "js/game.js": b"run()"},
        "broken-js": {"index.html": GOOD, "style.css": b"body{}"},  # js/game.js is gone
        "blank": {"index.html": b"<html><body></body></html>"},
        "cdn-only": {"index.html": b"<script src='https://cdn.example.com/phaser.js'></script>"},
    }
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada", "bo")}
        db.add_all(users.values())
        db.flush()
        for name, files in games.items():
            store.put_game(name, files)
            db.add(Game(name=name, owner_id=users["ada"].id, repo_url=f"https://github.com/x/{name}"))
        db.add(Game(name="no-page", owner_id=users["ada"].id, repo_url="https://github.com/x/no-page"))  # nothing stored
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, Session, store
    app.dependency_overrides.clear()


def codes(store, name, fetch=service.fetch_status):
    return [p.code for p in service.check(store, name, fetch)]


def test_what_a_check_finds(env, monkeypatch):
    client, tokens, Session, store = env
    assert codes(store, "lane-dash") == []
    assert codes(store, "cdn-only") == []  # scripts from other sites aren't ours to check
    assert codes(store, "broken-js") == ["missing_file"]
    assert codes(store, "blank") == ["blank_page"]
    assert codes(store, "no-page") == ["no_page"]
    monkeypatch.setattr(settings, "game_health_http", True)
    assert codes(store, "lane-dash", fetch=lambda url: 200) == []
    assert codes(store, "lane-dash", fetch=lambda url: 404) == ["not_loading"]
    assert codes(store, "lane-dash", fetch=lambda url: "ConnectTimeout") == ["not_loading"]


def test_three_failures_in_a_row_make_it_unhealthy(env):
    client, tokens, Session, store = env
    heard = []
    events.subscribe("game_unhealthy")(heard.append)
    try:
        with Session() as db:
            for _ in range(service.FAILS_IN_A_ROW + 1):
                row = service.check_and_record(db, store, "broken-js")
            assert (row.status, row.failures) == ("unhealthy", 4)
        assert heard == [{"game": "broken-js", "reason": "missing_file"}]  # once, when it became unhealthy
        store.put_file("broken-js", "js/game.js", b"run()", "text/javascript")  # the owner fixed it
        with Session() as db:
            row = service.check_and_record(db, store, "broken-js")
            assert (row.status, row.failures, row.problems, row.unhealthy_since) == ("ok", 0, None, None)
    finally:
        events.unsubscribe("game_unhealthy", heard.append)


def test_the_job_checks_due_games(env):
    client, tokens, Session, store = env
    with Session() as db:
        db.get(Game, "blank").hidden = "reported"  # hidden games are offline on purpose: not checked
        db.commit()
        assert service.check_due(db, store) == 4
        assert service.due(db) == []
        at = service.now()
        assert sorted(service.due(db, at + timedelta(minutes=11))) == ["broken-js", "no-page"]  # failing: sooner
        assert len(service.due(db, at + timedelta(minutes=21))) == 4
    # a new version starts again
    events.publish("game_updated", {"game": "broken-js", "owner_id": 2, "auto": True})
    with Session() as db:
        assert db.get(GameHealth, "broken-js").status == "unchecked" and service.due(db) == ["broken-js"]


def test_admins_and_owners_see_unhealthy_games(env):
    client, tokens, Session, store = env
    assert client.get("/admin/game-health", headers=tokens["ada"]).status_code == 404  # not an admin
    for _ in range(service.FAILS_IN_A_ROW):
        r = client.post("/admin/game-health/games/no-page/check", headers=tokens["boss"]).json()
    assert r["status"] == "unhealthy" and r["problems"][0]["code"] == "no_page"
    client.post("/admin/game-health/games/broken-js/check", headers=tokens["boss"])
    view = client.get("/admin/game-health", headers=tokens["boss"]).json()
    assert [g["game"] for g in view["games"]] == ["no-page", "broken-js"]
    assert view["counts"] == {"unhealthy": 1, "failing": 1, "unchecked": 3}
    # the owner sees only the unhealthy one
    assert [g["game"] for g in client.get("/game-health/mine", headers=tokens["ada"]).json()] == ["no-page"]
    assert client.get("/game-health/mine", headers=tokens["bo"]).json() == []
    assert client.post("/game-health/games/no-page/check", headers=tokens["bo"]).status_code == 403
    # the owner pushes a fix and asks for a check: asking again within a minute just shows the last one
    store.put_game("no-page", {"index.html": GOOD, "style.css": b"body{}", "js/game.js": b"run()"})
    assert client.post("/game-health/games/no-page/check", headers=tokens["ada"]).json()["status"] == "unhealthy"
    with Session() as db:
        db.get(GameHealth, "no-page").checked_at = service.now() - timedelta(minutes=2)
        db.commit()
    assert client.post("/game-health/games/no-page/check", headers=tokens["ada"]).json()["status"] == "ok"
    assert client.get("/game-health/mine", headers=tokens["ada"]).json() == []


def test_off_when_the_flag_is_off(env, monkeypatch):
    client, tokens, *_ = env
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/admin/game-health", headers=tokens["boss"]).status_code == 404
    assert client.get("/game-health/mine", headers=tokens["ada"]).status_code == 404
