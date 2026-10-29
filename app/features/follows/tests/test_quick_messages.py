"""Tests for canned quick messages between friends and opponents (Issue #63)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.follows import service
from app.features.follows.models import Follow
from app.main import app
from app.models import User
from app.security import create_token

PLAYERS = ("ada", "tunde", "chioma")


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
        users = [
            User(email=f"{n}@example.com", name=n.title(), character={"name": n.title(), "style": {}})
            for n in PLAYERS
        ]
        db.add_all(users)
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}

        # Make ada and tunde mutual friends
        db.add_all([
            Follow(follower_id=ids["ada"], target_user_id=ids["tunde"]),
            Follow(follower_id=ids["tunde"], target_user_id=ids["ada"]),
        ])
        db.commit()

    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def test_send_quick_message_between_friends(env):
    client, tokens, ids, _ = env
    res = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={
            "recipient_id": ids["tunde"],
            "preset": "GG! 🔥",
            "game_name": "relic-hunter",
            "challenge_code": "ch1234",
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["preset"] == "GG! 🔥"
    assert data["sender"]["name"] == "Ada"
    assert data["recipient_id"] == ids["tunde"]
    assert data["game_name"] == "relic-hunter"
    assert data["challenge_code"] == "ch1234"
    assert data["read_at"] is None


def test_reject_free_text_message(env):
    client, tokens, ids, _ = env
    res = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={
            "recipient_id": ids["tunde"],
            "preset": "Send me your phone number",
        },
    )
    assert res.status_code == 400
    assert res.json()["errors"][0]["code"] == "invalid_preset"


def test_reject_messages_to_strangers(env):
    client, tokens, ids, _ = env
    # Ada and Chioma are not friends
    res = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={
            "recipient_id": ids["chioma"],
            "preset": "Rematch?",
        },
    )
    assert res.status_code == 403
    assert res.json()["errors"][0]["code"] == "not_friends"


def test_cannot_message_self(env):
    client, tokens, ids, _ = env
    res = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={
            "recipient_id": ids["ada"],
            "preset": "GG! 🔥",
        },
    )
    assert res.status_code == 400
    assert res.json()["errors"][0]["code"] == "cant_message_self"


def test_mute_blocks_messages(env):
    client, tokens, ids, _ = env
    # Tunde mutes Ada
    mute_res = client.post(f"/friends/mute/{ids['ada']}", headers=tokens["tunde"])
    assert mute_res.status_code == 200
    assert mute_res.json()["muted"] is True

    # Check muted list
    list_muted = client.get("/friends/muted", headers=tokens["tunde"])
    assert list_muted.status_code == 200
    assert ids["ada"] in list_muted.json()

    # Ada tries to message Tunde -> 403 muted
    msg_res = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={"recipient_id": ids["tunde"], "preset": "Rematch?"},
    )
    assert msg_res.status_code == 403
    assert msg_res.json()["errors"][0]["code"] == "muted"

    # Tunde unmutes Ada
    unmute_res = client.delete(f"/friends/mute/{ids['ada']}", headers=tokens["tunde"])
    assert unmute_res.status_code == 200
    assert unmute_res.json()["muted"] is False

    # Ada can message Tunde again
    msg_res2 = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={"recipient_id": ids["tunde"], "preset": "Rematch?"},
    )
    assert msg_res2.status_code == 201


def test_rate_limit(env):
    client, tokens, ids, _ = env
    for _ in range(10):
        res = client.post(
            "/friends/messages",
            headers=tokens["ada"],
            json={"recipient_id": ids["tunde"], "preset": "🔥"},
        )
        assert res.status_code == 201

    # 11th message is rate limited
    res11 = client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={"recipient_id": ids["tunde"], "preset": "🔥"},
    )
    assert res11.status_code == 429
    assert res11.json()["errors"][0]["code"] == "rate_limited"


def test_list_and_mark_read(env):
    client, tokens, ids, _ = env
    client.post(
        "/friends/messages",
        headers=tokens["ada"],
        json={"recipient_id": ids["tunde"], "preset": "Beat my score 😏"},
    )

    # Tunde views messages
    list_res = client.get("/friends/messages", headers=tokens["tunde"])
    assert list_res.status_code == 200
    data = list_res.json()
    assert data["count"] >= 1
    msg = data["messages"][0]
    assert msg["preset"] == "Beat my score 😏"
    assert msg["sender"]["name"] == "Ada"

    # Mark as read
    read_res = client.post(f"/friends/messages/{msg['id']}/read", headers=tokens["tunde"])
    assert read_res.status_code == 204
