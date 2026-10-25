from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import security
from app.config import settings
from app.live import create_ticket
from app.models import User
from tests.test_publish import env  # noqa: F401  (a database with two players, ada and tunde)


def claims(token):
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def token_with(user_id, left, remember=False, **extra):
    """A sign-in with `left` of its life remaining."""
    exp = datetime.now(timezone.utc) + left
    body = {"sub": str(user_id), "exp": exp, **extra}
    if remember:
        body["rem"] = 1
    return jwt.encode(body, settings.jwt_secret, algorithm="HS256")


def ada_id(client_env):
    from app.db import get_db

    db = next(client_env.app.dependency_overrides[get_db]())
    return db.query(User).filter(User.email == "ada@example.com").one().id


def test_a_sign_in_lasts_a_week_and_staying_signed_in_lasts_a_year():
    now = datetime.now(timezone.utc)
    short = claims(security.create_token(1))
    long = claims(security.create_token(1, remember=True))
    assert "rem" not in short and long["rem"] == 1
    assert timedelta(days=6, hours=23) < datetime.fromtimestamp(short["exp"], timezone.utc) - now <= timedelta(days=7, minutes=1)
    assert timedelta(days=364, hours=23) < datetime.fromtimestamp(long["exp"], timezone.utc) - now <= timedelta(days=365, minutes=1)


def test_register_login_and_google_hand_the_choice_to_the_token(env, monkeypatch):
    client, *_ = env
    body = {"email": "new@example.com", "password": "password123", "name": "New"}
    plain = client.post("/auth/register", json=body).json()["access_token"]
    assert "rem" not in claims(plain)
    kept = client.post("/auth/login", json={"email": "new@example.com", "password": "password123", "remember": True}).json()["access_token"]
    assert claims(kept)["rem"] == 1
    assert "rem" not in claims(client.post("/auth/login", json={"email": "new@example.com", "password": "password123"}).json()["access_token"])
    monkeypatch.setattr(settings, "google_client_id", "x")
    monkeypatch.setattr("app.routers.auth.g_id_token.verify_oauth2_token", lambda *a, **k: {"email": "g@example.com", "email_verified": True, "sub": "g1", "name": "G"})
    assert claims(client.post("/auth/google", json={"id_token": "t", "remember": True}).json()["access_token"])["rem"] == 1
    assert "rem" not in claims(client.post("/auth/google", json={"id_token": "t"}).json()["access_token"])


def test_visiting_renews_a_sign_in_that_is_past_half_its_life(env):
    client, *_ = env
    uid = ada_id(client)
    week = timedelta(days=7)
    old = token_with(uid, week * 0.4)  # three days left of seven
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {old}"})
    fresh = res.headers["X-Refreshed-Token"]
    assert datetime.fromtimestamp(claims(fresh)["exp"], timezone.utc) - datetime.now(timezone.utc) > timedelta(days=6, hours=23)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200  # the new one works
    # one with plenty of time left is left alone, so it is not renewed on every request
    assert "X-Refreshed-Token" not in client.get("/auth/me", headers={"Authorization": f"Bearer {token_with(uid, week * 0.9)}"}).headers


def test_staying_signed_in_is_kept_when_renewing(env):
    client, *_ = env
    uid = ada_id(client)
    year = timedelta(days=365)
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token_with(uid, year * 0.4, remember=True)}"})
    fresh = claims(res.headers["X-Refreshed-Token"])
    assert fresh["rem"] == 1 and datetime.fromtimestamp(fresh["exp"], timezone.utc) - datetime.now(timezone.utc) > timedelta(days=364)
    # a week-long sign-in is not turned into a year
    short = claims(client.get("/auth/me", headers={"Authorization": f"Bearer {token_with(uid, timedelta(days=2))}"}).headers["X-Refreshed-Token"])
    assert "rem" not in short


def test_an_old_24_hour_sign_in_becomes_a_week_on_its_next_visit(env):
    client, *_ = env
    uid = ada_id(client)
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {token_with(uid, timedelta(hours=5))}"})
    assert datetime.fromtimestamp(claims(res.headers["X-Refreshed-Token"])["exp"], timezone.utc) - datetime.now(timezone.utc) > timedelta(days=6)


def test_someone_away_longer_than_the_lifetime_is_signed_out_and_nothing_is_renewed(env):
    from app.db import get_db

    client, *_ = env
    uid = ada_id(client)
    gone = token_with(uid, timedelta(seconds=-5))
    res = client.get("/auth/me", headers={"Authorization": f"Bearer {gone}"})
    assert res.status_code == 401 and "X-Refreshed-Token" not in res.headers
    assert security.renewed_token(gone, next(client.app.dependency_overrides[get_db]())) is None


def test_failed_requests_blocked_players_and_other_tokens_are_not_renewed(env):
    from app.db import get_db

    client, *_ = env
    uid = ada_id(client)
    db = next(client.app.dependency_overrides[get_db]())
    near_end = token_with(uid, timedelta(hours=1))
    # a request that fails does not renew (here: a route that does not exist)
    assert "X-Refreshed-Token" not in client.get("/nope", headers={"Authorization": f"Bearer {near_end}"}).headers
    # a blocked player's token is not renewed
    db.query(User).filter(User.id == uid).update({"blocked_at": datetime.now(timezone.utc)})
    db.commit()
    assert security.renewed_token(near_end, db) is None
    db.query(User).filter(User.id == uid).update({"blocked_at": None})
    db.commit()
    # a live-room ticket or a game session is not a sign-in, so never renewed
    ticket = create_ticket(uid, "hangout")
    assert security.renewed_token(ticket, db) is None
    assert security.renewed_token(security.create_game_session(uid, "hangout"), db) is None
    assert security.renewed_token("not a token", db) is None
    assert security.renewed_token(near_end, db) is not None


def test_the_browser_may_read_the_renewal_header(env):
    client, *_ = env
    res = client.options("/auth/me", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert res.status_code in (200, 204)
    uid = ada_id(client)
    shown = client.get("/auth/me", headers={"Authorization": f"Bearer {token_with(uid, timedelta(hours=1))}", "Origin": "http://localhost:5173"})
    assert "x-refreshed-token" in shown.headers.get("access-control-expose-headers", "").lower()
