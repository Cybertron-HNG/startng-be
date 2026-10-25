"""F1: GitHub's signed push webhook updates a game straight away. Payloads here are made up and signed with a test
secret: nothing talks to GitHub."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.github_app import service
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import Game, User
from app.routers import games as games_router
from tests.test_publish import make_zip

SECRET = "test-secret"
SHA = "a" * 40


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "features", "github_app")
    monkeypatch.setattr(settings, "github_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "github_app_slug", "start-ng")
    monkeypatch.setattr(settings, "games_base_domain", "start.ng")
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
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "get_game_store", lambda: store)
    archives = {"zip": make_zip({"index.html": "<canvas></canvas><script>v2()</script>"})}
    monkeypatch.setattr(games_router, "download_repo", lambda repo: archives["zip"])
    with Session() as db:
        ada = User(email="ada@example.com", name="Ada", google_sub="g-ada")
        db.add(ada)
        db.flush()
        db.add(Game(name="lane-dash", owner_id=ada.id, repo_url="https://github.com/Ada/Lane-Dash", last_commit="b" * 40))
        db.add(Game(name="star-hop", owner_id=ada.id, repo_url="https://github.com/ada/star-hop", branch="release"))
        db.commit()
    store.put_game("lane-dash", {"index.html": b"<h1>v1</h1>"})
    yield TestClient(app), Session, store, archives
    app.dependency_overrides.clear()


def push(client, repo="https://github.com/ada/lane-dash", ref="refs/heads/main", sha=SHA, secret=SECRET, event="push", **extra):
    body = json.dumps({"ref": ref, "after": sha, "repository": {"html_url": repo, "default_branch": "main"}, **extra}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/github/webhook", content=body, headers={
        "X-Hub-Signature-256": sig, "X-GitHub-Event": event, "Content-Type": "application/json"})


def test_a_push_updates_the_game_straight_away(env):
    client, Session, store, archives = env
    res = push(client)  # GitHub sends the repo's own capitals; the match ignores them
    assert res.status_code == 202 and res.json()["updating"] == ["lane-dash"]
    assert (store.root / "lane-dash" / "index.html").read_text() == "<canvas></canvas><script>v2()</script>"
    with Session() as db:
        game = db.get(Game, "lane-dash")
        assert game.last_commit == SHA and game.checked_at is not None and game.update_problem is None
    assert push(client).json()["updating"] == []  # GitHub sends again: that commit is live already


@pytest.mark.parametrize("kwargs", [
    {"ref": "refs/heads/dev"},  # not the branch the game is published from
    {"ref": "refs/tags/v1"},
    {"sha": "0" * 40},  # the branch was deleted
    {"deleted": True},
    {"repo": "https://github.com/someone/else"},
    {"repo": "https://github.com/ada/star-hop"},  # published from "release", and this push is to main
])
def test_pushes_that_dont_update_anything(env, kwargs):
    client, *_ = env
    assert push(client, **kwargs).json()["updating"] == []


def test_a_game_on_its_own_branch(env):
    client, *_ = env
    assert push(client, repo="https://github.com/ada/star-hop", ref="refs/heads/release").json()["updating"] == ["star-hop"]


def test_a_push_that_cant_go_live_is_shown_to_the_owner(env):
    client, Session, store, archives = env
    archives["zip"] = make_zip({"readme.md": "no game here"})
    push(client)
    with Session() as db:
        game = db.get(Game, "lane-dash")
        assert game.update_problem["commit"] == SHA and game.last_commit == SHA  # not tried again until a new push
    assert (store.root / "lane-dash" / "index.html").read_text() == "<h1>v1</h1>"  # the live version stays


def test_only_signed_requests(env, monkeypatch):
    client, *_ = env
    assert push(client, secret="wrong").status_code == 401
    assert client.post("/github/webhook", content=b"{}", headers={"X-GitHub-Event": "push"}).status_code == 401
    assert push(client, event="ping").status_code == 200
    assert push(client, event="installation").json()["updating"] == []
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    assert push(client).status_code == 503


def test_the_install_link_and_the_flag(env, monkeypatch):
    client, *_ = env
    assert client.get("/github/app").json() == {"install_url": "https://github.com/apps/start-ng/installations/new"}
    monkeypatch.setattr(settings, "features", "")
    assert push(client).status_code == 404 and client.get("/github/app").status_code == 404
