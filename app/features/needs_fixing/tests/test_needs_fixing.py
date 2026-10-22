from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.config import settings
from app.db import Base, get_db
from app.game_store import get_game_store
from app.main import app
from app.models import Activity, Game, User
from app.security import create_token

NOON = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)
BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]


class Store:
    def __init__(self):
        self.offline = set()

    def set_hidden(self, name, hidden):
        (self.offline.add if hidden else self.offline.discard)(name)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(tournaments, "now", lambda: NOON)
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "needs_fixing")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    store = Store()

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_game_store] = lambda: store
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "dev", "other")}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name=f"g{i}", owner_id=users["dev"].id, repo_url=f"https://github.com/dev/g{i}", leaderboard=BOARD) for i in range(4)])
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, Session, store
    app.dependency_overrides.clear()


NOTE = {"note": "The game freezes on level 2 and the back button doesn't work."}


def test_the_whole_round(env):
    client, tokens, Session, store = env
    marked = client.post("/admin/needs-fixing/games/g1", json=NOTE, headers=tokens["boss"])
    assert marked.status_code == 201 and marked.json()["status"] == "waiting_owner"
    rid = marked.json()["id"]
    # hidden everywhere: off the list, offline, owner told why
    assert "g1" not in [g["name"] for g in client.get("/games").json()] and store.offline == {"g1"}
    mine = client.get("/needs-fixing/mine", headers=tokens["dev"]).json()
    assert [(m["game"], m["note"], m["owner"], m["created_by"]) for m in mine] == [("g1", NOTE["note"], None, None)]
    assert {g["name"]: g["hidden"] for g in client.get("/games/mine", headers=tokens["dev"]).json()}["g1"] == "needs_fixing"
    # only the owner can say it's fixed
    assert client.post(f"/needs-fixing/{rid}/fixed", json={}, headers=tokens["other"]).status_code == 404
    fixed = client.post(f"/needs-fixing/{rid}/fixed", json={"note": "Fixed the freeze, pushed."}, headers=tokens["dev"]).json()
    assert fixed["status"] == "owner_fixed" and store.offline == {"g1"}  # still hidden until an admin looks
    # an admin sends it back, the owner fixes it again, an admin puts it back
    back = client.post(f"/admin/needs-fixing/{rid}/reopen", json={"note": "The back button still does nothing."}, headers=tokens["boss"]).json()
    assert (back["status"], back["rounds"]) == ("waiting_owner", 2)
    client.post(f"/needs-fixing/{rid}/fixed", json={}, headers=tokens["dev"])
    assert client.get("/admin/needs-fixing", headers=tokens["boss"]).json()[0]["status"] == "owner_fixed"
    assert client.post(f"/admin/needs-fixing/{rid}/approve", headers=tokens["boss"]).json()["status"] == "closed"
    assert "g1" in [g["name"] for g in client.get("/games").json()] and store.offline == set()
    assert client.get("/needs-fixing/mine", headers=tokens["dev"]).json() == []
    with Session() as db:
        assert db.scalars(select(Activity.action).order_by(Activity.id)).all() == [
            "game.needs_fixing", "game.owner_fixed", "game.fix_sent_back", "game.owner_fixed", "game.fix_approved"]


def test_one_open_request_per_game_and_admins_only(env):
    client, tokens, *_ = env
    assert client.post("/admin/needs-fixing/games/g2", json=NOTE, headers=tokens["dev"]).status_code == 404  # not an admin
    assert client.post("/admin/needs-fixing/games/g2", json={"note": "bad"}, headers=tokens["boss"]).status_code == 422  # too short
    assert client.post("/admin/needs-fixing/games/g2", json=NOTE, headers=tokens["boss"]).status_code == 201
    assert client.post("/admin/needs-fixing/games/g2", json=NOTE, headers=tokens["boss"]).status_code == 409
    assert client.post("/admin/needs-fixing/games/nope", json=NOTE, headers=tokens["boss"]).status_code == 404


def test_the_reports_tab_cant_skip_the_fix_review(env):
    client, tokens, *_ = env
    client.post("/admin/needs-fixing/games/g3", json=NOTE, headers=tokens["boss"])
    res = client.post("/admin/games/g3/restore", json={}, headers=tokens["boss"])
    assert res.status_code == 409 and "needs fixing" in res.json()["detail"]


def test_a_game_flagged_too_stays_hidden_after_the_fix(env):
    client, tokens, *_ = env
    rid = client.post("/admin/needs-fixing/games/g0", json=NOTE, headers=tokens["boss"]).json()["id"]
    client.post("/admin/games/g0/flag", json={"reason": "obscene"}, headers=tokens["boss"])  # a stronger reason
    client.post(f"/admin/needs-fixing/{rid}/approve", headers=tokens["boss"])
    assert "g0" not in [g["name"] for g in client.get("/games").json()]


def test_off_while_the_flag_is_off(env, monkeypatch):
    client, tokens, *_ = env
    monkeypatch.setattr(settings, "features", "")
    assert client.post("/admin/needs-fixing/games/g1", json=NOTE, headers=tokens["boss"]).status_code == 404
    assert client.get("/needs-fixing/mine", headers=tokens["dev"]).status_code == 404
