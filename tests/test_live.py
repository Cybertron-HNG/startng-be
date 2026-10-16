import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app import live
from app.db import Base, get_db
from datetime import datetime, timedelta, timezone

from app.live import BAD_TICKET, REPLACED, create_ticket
from app.routers import live as live_router
from app.main import app
from app.models import Game, User
from app.routers.live import get_sessionmaker
from app.security import create_token

STYLE = {"primaryColor": "#a55eea", "body": "female"}


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

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_sessionmaker] = lambda: Session
    monkeypatch.setattr(live, "MOVE_EVERY", 0)  # don't drop quick test moves
    monkeypatch.setattr(live, "rooms", {})
    with Session() as db:
        ada = User(email="ada@example.com", name="Ada", character={"name": "Runner Ada", "style": STYLE})
        tunde = User(email="tunde@example.com", name="Tunde")
        db.add_all([ada, tunde])
        db.flush()
        db.add(Game(name="hangout", owner_id=ada.id, repo_url="https://github.com/ada/hangout"))
        db.commit()
        ids = {"ada": ada.id, "tunde": tunde.id}
    yield TestClient(app), ids
    app.dependency_overrides.clear()


def url(user_id, game="hangout"):
    return f"/games/{game}/live?ticket={create_ticket(user_id, game)}"


def test_ticket_needs_sign_in_and_a_real_game(env):
    client, ids = env
    auth = {"Authorization": f"Bearer {create_token(ids['ada'])}"}
    assert client.post("/games/hangout/ticket").status_code == 401
    assert client.post("/games/nope/ticket", headers=auth).status_code == 404
    res = client.post("/games/hangout/ticket", headers=auth)
    assert res.status_code == 200 and res.json()["expires_in"] == 60


def test_a_ticket_is_not_a_sign_in(env):
    client, ids = env
    ticket = create_ticket(ids["ada"], "hangout")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401


@pytest.mark.parametrize("path", [
    "/games/hangout/live",
    "/games/hangout/live?ticket=nonsense",
    "/games/hangout/live?ticket={other_game}",
])
def test_bad_tickets_are_turned_away(env, path):
    client, ids = env
    path = path.format(other_game=create_ticket(ids["ada"], "lanedash"))
    with client.websocket_connect(path) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == BAD_TICKET


def test_sign_in_token_is_not_a_ticket(env):
    client, ids = env
    with client.websocket_connect(f"/games/hangout/live?ticket={create_token(ids['ada'])}") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == BAD_TICKET


def test_two_players_see_each_other(env):
    client, ids = env
    with client.websocket_connect(url(ids["ada"])) as ada:
        welcome = ada.receive_json()
        assert welcome["type"] == "welcome" and welcome["you"] == ids["ada"]
        assert [(p["name"], p["style"]) for p in welcome["players"]] == [("Runner Ada", STYLE)]

        with client.websocket_connect(url(ids["tunde"])) as tunde:
            seen = tunde.receive_json()
            assert {p["name"] for p in seen["players"]} == {"Runner Ada", "Tunde"}
            joined = ada.receive_json()
            assert joined["type"] == "join" and joined["player"]["name"] == "Tunde"
            assert joined["player"]["style"] is None  # no character yet: games use the default look

            # Positions are kept inside the park, and junk is ignored.
            tunde.send_text("not json")
            tunde.send_json({"type": "move", "x": "far", "z": 0, "ry": 0})
            tunde.send_json({"type": "move", "x": 999, "z": -3.5, "ry": 1.2, "moving": True})
            moved = ada.receive_json()
            assert moved == {"type": "move", "id": ids["tunde"], "x": 60.0, "z": -3.5, "ry": 1.2, "moving": True}

            tunde.send_json({"type": "wave"})
            tunde.send_json({"type": "wave"})  # too soon after the first: dropped
            assert ada.receive_json() == {"type": "wave", "id": ids["tunde"]}

            ada.send_json({"type": "wave"})
            assert tunde.receive_json() == {"type": "wave", "id": ids["ada"]}

        left = ada.receive_json()
        assert left["type"] == "leave" and left["id"] == ids["tunde"] and left["seat"] is not None  # back to their seat


def test_joining_again_replaces_the_old_connection(env):
    client, ids = env
    with client.websocket_connect(url(ids["ada"])) as first:
        first.receive_json()
        with client.websocket_connect(url(ids["ada"])) as second:
            assert [p["id"] for p in second.receive_json()["players"]] == [ids["ada"]]
            with pytest.raises(WebSocketDisconnect) as closed:
                first.receive_json()
            assert closed.value.code == REPLACED


def test_seats_for_the_most_recently_active(env, monkeypatch):
    client, ids = env
    monkeypatch.setattr(live, "MAX_SEATS", 2)
    monkeypatch.setattr(live_router, "MAX_SEATS", 2)
    Session = app.dependency_overrides[get_sessionmaker]()
    now = datetime.now(timezone.utc)
    with Session() as db:
        chioma = User(email="chioma@example.com", name="Chioma", last_seen_at=now)
        musa = User(email="musa@example.com", name="Musa", last_seen_at=now - timedelta(days=30))
        db.add_all([chioma, musa])
        db.get(User, ids["ada"]).last_seen_at = now - timedelta(hours=1)
        db.commit()
        ids["chioma"], ids["musa"] = chioma.id, musa.id

    with client.websocket_connect(url(ids["ada"])) as ada:
        welcome = ada.receive_json()
        seats = {r["name"]: r["seat"] for r in welcome["seats"]}
        # Chioma (seen just now) and Ada (an hour ago) get the two seats; Musa and Tunde don't.
        assert seats == {"Chioma": 0, "Runner Ada": 1}
        me = welcome["players"][0]
        assert me["seat"] == 1 and me["x"] is None and me["z"] is None  # standing at their seat

        with client.websocket_connect(url(ids["musa"])) as musa:
            joined = musa.receive_json()["players"]
            musa_me = next(p for p in joined if p["id"] == ids["musa"])
            assert musa_me["seat"] is None and musa_me["x"] is not None  # no seat left: starts mid-park
            assert ada.receive_json()["type"] == "join"
        left = ada.receive_json()
        assert left == {"type": "leave", "id": ids["musa"], "seat": None}


def test_activity_is_recorded(env):
    client, ids = env
    auth = {"Authorization": f"Bearer {create_token(ids['tunde'])}"}
    Session = app.dependency_overrides[get_sessionmaker]()

    def seen():
        with Session() as db:
            return db.get(User, ids["tunde"]).last_seen_at

    assert seen() is None
    client.get("/auth/me", headers=auth)
    first = seen()
    assert first is not None
    client.get("/auth/me", headers=auth)
    assert seen() == first  # not rewritten on every request
    client.post("/games/hangout/ticket", headers=auth)
    assert seen() > first  # coming into the Hangout always counts
