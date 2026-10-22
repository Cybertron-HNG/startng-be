from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.config import settings
from app.db import Base, get_db
from app.features.duplicates import service
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import Game, User
from app.security import create_token

NOON = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)
PAGE = b"<!doctype html><h1>Lane Dash</h1><script src='game.js'></script>"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tournaments, "now", lambda: NOON)
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "duplicates")
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
    # The publish event handler opens its own session and store: point them at the test ones.
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "get_game_store", lambda: store)

    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    files = {
        "lane-dash": {"index.html": PAGE, "game.js": b"run()"},  # the original
        "lane-dash-2": {"index.html": b"\n" + PAGE.replace(b"><", b">  <"), "game.js": b"run()"},  # the same page re-spaced, by another dev
        "pixel-run": {"index.html": b"<h1>Pixel Run</h1>", "game.js": b"jump()"},
        "pixl-run": {"index.html": b"<h1>Something else</h1>", "game.js": b"other()"},  # one letter apart from pixel-run
        "maze-mice": {"index.html": b"<h1>Maze</h1>", "maze.js": b"x"},
    }
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada", "copycat")}
        db.add_all(users.values())
        db.flush()
        for i, (name, content) in enumerate(files.items()):
            store.put_game(name, content)
            owner = users["copycat"] if name in ("lane-dash-2", "pixl-run") else users["ada"]
            db.add(Game(name=name, owner_id=owner.id, repo_url=f"https://github.com/x/{name}", created_at=t0 + timedelta(days=i)))
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, Session, store
    app.dependency_overrides.clear()


def scan(client, tokens):
    assert client.post("/admin/duplicates/scan", headers=tokens["boss"]).json() == {"scanned": 5}


def test_an_exact_copy_has_the_same_files(env):
    client, tokens, Session, store = env
    store.put_game("lane-dash-copy", {"index.html": PAGE, "game.js": b"run()"})
    with Session() as db:
        db.add(Game(name="lane-dash-copy", owner_id=3, repo_url="https://github.com/x/copy"))
        db.commit()
    client.post("/admin/duplicates/scan", headers=tokens["boss"])
    pairs = {(p["a"]["name"], p["b"]["name"]): sorted(p["reasons"]) for p in client.get("/admin/duplicates", headers=tokens["boss"]).json()}
    assert pairs[("lane-dash", "lane-dash-copy")] == ["same_files", "same_page", "similar_name"]


def test_names():
    assert {service.name_key(n) for n in ("lane-dash", "lane-dash-2", "lanedash-copy", "Lane-Dash-v3")} == {"lanedash"}
    assert service.one_apart("pixelrun", "pixlrun") and not service.one_apart("snake", "snaky")  # short names never


def test_finds_the_copies_oldest_first(env):
    client, tokens, *_ = env
    scan(client, tokens)
    got = {(p["a"]["name"], p["b"]["name"]): (sorted(p["reasons"]), p["same_owner"]) for p in client.get("/admin/duplicates", headers=tokens["boss"]).json()}
    assert got == {
        # the page only differs in spacing, so it's the same page; its size differs, so not the same files
        ("lane-dash", "lane-dash-2"): (["same_page", "similar_name"], False),
        ("pixel-run", "pixl-run"): (["similar_name"], False),
    }


def test_keep_hide_show_delete(env):
    client, tokens, Session, store = env
    scan(client, tokens)
    # keep: the pair stops showing
    assert client.post("/admin/duplicates/keep", json={"a": "pixl-run", "b": "pixel-run"}, headers=tokens["boss"]).status_code == 204
    assert [p["b"]["name"] for p in client.get("/admin/duplicates", headers=tokens["boss"]).json()] == ["lane-dash-2"]
    # hide as a copy: gone from start.ng, the owner sees of what
    assert client.post("/admin/duplicates/games/lane-dash-2/hide", json={"of": "lane-dash"}, headers=tokens["boss"]).status_code == 204
    assert "lane-dash-2" not in [g["name"] for g in client.get("/games").json()]
    assert client.get("/duplicates/mine", headers=tokens["copycat"]).json() == [{"game": "lane-dash-2", "of": "lane-dash"}]
    assert client.get("/admin/duplicates/hidden", headers=tokens["boss"]).json()[0]["of"] == "lane-dash"
    assert client.post("/admin/games/lane-dash-2/restore", json={}, headers=tokens["boss"]).status_code == 409  # not from Reports
    # put back: live again, and the pair counts as kept
    assert client.post("/admin/duplicates/games/lane-dash-2/show", headers=tokens["boss"]).status_code == 204
    assert "lane-dash-2" in [g["name"] for g in client.get("/games").json()]
    assert client.get("/admin/duplicates", headers=tokens["boss"]).json() == []
    # delete: files and game gone
    assert client.delete("/admin/duplicates/games/lane-dash-2", headers=tokens["boss"]).status_code == 204
    assert store.paths("lane-dash-2") == []
    with Session() as db:
        assert db.get(Game, "lane-dash-2") is None


def test_publishing_fingerprints_a_game(env):
    client, tokens, Session, store = env
    from app import events
    events.publish("game_published", {"game": "maze-mice", "owner_id": 2})
    with Session() as db:
        fp = db.get(service.Fingerprint, "maze-mice")
        assert fp is not None and fp.name_key == "mazemice" and fp.files_hash and fp.index_hash


def test_admins_only_and_flag(env, monkeypatch):
    client, tokens, *_ = env
    assert client.get("/admin/duplicates", headers=tokens["ada"]).status_code == 404
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/admin/duplicates", headers=tokens["boss"]).status_code == 404
