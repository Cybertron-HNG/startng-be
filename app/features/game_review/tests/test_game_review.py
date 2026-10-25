"""F2: a new game waits for an admin before it's listed."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import storage as bucket
from app.config import settings
from app.db import Base, get_db
from app.features.email.models import Email
from app.features.game_review import service
from app.features.game_review.models import GameReview
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import Game, User
from app.routers import games as games_router
from app.security import create_token
from tests.test_publish import GAME, make_zip

REPO = "https://github.com/ada/lane-dash"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "games_base_domain", "start.ng")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "game_review,email")
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
    monkeypatch.setattr(service, "SessionLocal", Session)  # the publish event handlers open their own session
    archives = {"zip": make_zip(GAME)}
    monkeypatch.setattr(games_router, "download_repo", lambda repo: archives["zip"])
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada")}
        db.add_all(users.values())
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, Session, store, archives
    app.dependency_overrides.clear()


def publish(client, who, tokens):
    res = client.post("/games/publish", json={"repo_url": REPO}, headers=tokens[who])
    assert res.status_code == 200, res.text
    return res.json()


def mail(Session):
    with Session() as db:
        return [(r.to_email, r.notes[0]["kind"]) for r in db.scalars(select(Email).order_by(Email.id)).all()]


def listed(client):
    return [g["name"] for g in client.get("/games").json()]


def test_a_new_game_waits_then_goes_live(env):
    client, tokens, Session, store, archives = env
    body = publish(client, "ada", tokens)
    assert (body["name"], body["created"], body["hidden"]) == ("lane-dash", True, "in_review")
    assert listed(client) == []  # not on start.ng's lists
    assert not (store.root / bucket.HIDDEN_PREFIX / "lane-dash").exists()  # but its address works, to review it
    assert mail(Session) == [("ada@example.com", "game_review_received"), ("boss@example.com", "game_review_waiting")]
    queue = client.get("/admin/game-review", headers=tokens["boss"]).json()
    assert [r["game"] for r in queue["pending"]] == ["lane-dash"] and "doesnt_load" in queue["reasons"]
    assert client.get("/admin/game-review", headers=tokens["ada"]).status_code == 404  # admins only
    assert [r["status"] for r in client.get("/game-review/mine", headers=tokens["ada"]).json()] == ["pending"]

    r = client.post("/admin/game-review/lane-dash/approve", headers=tokens["boss"])
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert listed(client) == ["lane-dash"]
    assert mail(Session)[-1] == ("ada@example.com", "game_review_approved")
    assert client.get("/game-review/mine", headers=tokens["ada"]).json() == []
    # only the first publish is reviewed: an update goes live straight away
    archives["zip"] = make_zip({"index.html": "<canvas></canvas><script>v2()</script>"})
    assert publish(client, "ada", tokens)["hidden"] is None
    assert listed(client) == ["lane-dash"]


def test_rejected_then_fixed_comes_back(env):
    client, tokens, Session, store, archives = env
    publish(client, "ada", tokens)
    no = client.post("/admin/game-review/lane-dash/reject", json={"reason": "nope"}, headers=tokens["boss"])
    assert no.status_code == 422
    no = client.post("/admin/game-review/lane-dash/reject", json={"reason": "other"}, headers=tokens["boss"])
    assert no.status_code == 422  # "something else" needs a note
    r = client.post("/admin/game-review/lane-dash/reject", json={"reason": "controls", "note": "Arrows don't move it"},
                    headers=tokens["boss"]).json()
    assert (r["status"], r["reason_label"]) == ("rejected", "The controls don't work (or don't work on phones)")
    assert listed(client) == []
    assert mail(Session)[-1] == ("ada@example.com", "game_review_rejected")
    mine = client.get("/game-review/mine", headers=tokens["ada"]).json()
    assert (mine[0]["status"], mine[0]["note"]) == ("rejected", "Arrows don't move it")
    # the owner pushes a fix: back in the queue, and the admins are told
    publish(client, "ada", tokens)
    with Session() as db:
        review = db.get(GameReview, "lane-dash")
        assert (review.status, review.rounds) == ("pending", 2)
    assert mail(Session)[-1] == ("boss@example.com", "game_review_waiting")
    assert client.post("/admin/game-review/lane-dash/approve", headers=tokens["boss"]).json()["status"] == "approved"
    assert listed(client) == ["lane-dash"]


def test_games_from_before_and_from_admins_dont_wait(env, monkeypatch):
    client, tokens, Session, store, archives = env
    monkeypatch.setattr(settings, "features", "email")  # published while reviews were off
    publish(client, "ada", tokens)
    monkeypatch.setattr(settings, "features", "game_review,email")
    archives["zip"] = make_zip({"index.html": "<canvas></canvas><script>v2()</script>"})
    assert publish(client, "ada", tokens)["hidden"] is None
    assert listed(client) == ["lane-dash"]
    # a super admin's own new game is approved straight away
    res = client.post("/games/publish", json={"repo_url": "https://github.com/boss/star-hop"}, headers=tokens["boss"])
    assert res.json()["hidden"] is None and "star-hop" in listed(client)
    with Session() as db:
        assert db.get(GameReview, "star-hop").status == "approved" and db.get(GameReview, "lane-dash") is None


def test_a_game_approved_through_a_link_waits_too(env):
    client, tokens, Session, store, archives = env
    res = client.post("/games/publish", json={"repo_url": REPO})  # without signing in
    code = res.json()["approve_url"].rsplit("/approve/", 1)[1]
    body = client.post(f"/games/publish/{code}/approve", headers=tokens["ada"]).json()
    assert body["hidden"] == "in_review" and listed(client) == []


def test_developer_emails_off(env, monkeypatch):
    client, tokens, Session, store, archives = env
    monkeypatch.setattr(settings, "dev_emails", False)
    publish(client, "ada", tokens)
    client.post("/admin/game-review/lane-dash/approve", headers=tokens["boss"])
    assert mail(Session) == [("boss@example.com", "game_review_waiting")]


def test_email_wording(env):
    note = service._rejected({"game": "lane-dash", "reason": "copy", "note": "<b>same as dave</b>"})
    assert note.subject == "Lane Dash wasn't approved yet"
    assert "It's a copy of another game" in note.text and "&lt;b&gt;same as dave&lt;/b&gt;" in note.html
