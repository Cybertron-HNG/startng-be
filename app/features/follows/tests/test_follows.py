"""Friends & follows (S2): follows, friends, invite links, limits, privacy and the follow_created event."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.follows import service
from app.features.follows.models import Follow, FriendInvite
from app.main import app
from app.models import User
from app.security import create_token

PLAYERS = ("ada", "tunde", "chioma", "musa")


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
        users = [User(email=f"{n}@example.com", name=n.title(), character={"name": n.title(), "style": {}}) for n in PLAYERS]
        db.add_all(users)
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


@pytest.fixture
def heard():
    """Every follow_created event published during the test."""
    got = []

    def listen(payload):
        got.append((payload["follower_id"], payload["target_user_id"]))

    events.subscribe("follow_created")(listen)
    yield got
    events.unsubscribe("follow_created", listen)


def block(Session, user_id):
    with Session() as db:
        db.get(User, user_id).blocked_at = datetime.now(timezone.utc)
        db.commit()


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, ids, _ = env
    monkeypatch.setattr(settings, "features", "")
    assert client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"]).status_code == 404
    assert client.get("/me/friends", headers=tokens["ada"]).status_code == 404
    assert client.post("/friend-invites", headers=tokens["ada"]).status_code == 404


def test_signing_in_is_needed_to_follow(env):
    client, _, ids, _ = env
    assert client.put(f"/follows/{ids['tunde']}").status_code == 401
    assert client.get("/me/following").status_code == 401
    assert client.post("/friend-invites").status_code == 401


def test_follow_then_follow_back_makes_friends(env, heard):
    client, tokens, ids, _ = env
    res = client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"])
    assert res.status_code == 200
    assert res.json() == {"user_id": ids["tunde"], "followers": 1, "following": 0, "you_follow": True, "friends": False}
    assert client.get("/me/friends", headers=tokens["ada"]).json()["count"] == 0

    res = client.put(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    assert res.json()["friends"] is True
    friends = client.get("/me/friends", headers=tokens["ada"]).json()
    assert [p["name"] for p in friends["people"]] == ["Tunde"]
    assert heard == [(ids["ada"], ids["tunde"]), (ids["tunde"], ids["ada"])]


def test_following_twice_is_not_an_error_and_fires_once(env, heard):
    client, tokens, ids, Session = env
    assert client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"]).status_code == 200
    assert client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"]).status_code == 200
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(Follow)) == 1
    assert len(heard) == 1


def test_cant_follow_yourself_a_stranger_or_a_blocked_player(env):
    client, tokens, ids, Session = env
    res = client.put(f"/follows/{ids['ada']}", headers=tokens["ada"])
    assert res.status_code == 400 and res.json()["errors"][0]["code"] == "cant_follow_self"
    res = client.put("/follows/9999", headers=tokens["ada"])
    assert res.status_code == 404 and res.json()["errors"][0]["code"] == "player_not_found"
    block(Session, ids["musa"])
    assert client.put(f"/follows/{ids['musa']}", headers=tokens["ada"]).status_code == 404
    assert client.get(f"/follows/{ids['musa']}").status_code == 404


def test_unfollow_ends_a_friendship(env):
    client, tokens, ids, _ = env
    client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"])
    client.put(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    assert client.delete(f"/follows/{ids['tunde']}", headers=tokens["ada"]).status_code == 204
    assert client.get("/me/friends", headers=tokens["tunde"]).json()["count"] == 0
    assert client.get("/me/followers", headers=tokens["tunde"]).json()["count"] == 0
    assert client.get("/me/following", headers=tokens["tunde"]).json()["count"] == 1
    assert client.delete(f"/follows/{ids['tunde']}", headers=tokens["ada"]).status_code == 204  # nothing left: fine


def test_lists_show_names_and_characters_never_emails(env):
    client, tokens, ids, _ = env
    client.put(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    client.put(f"/follows/{ids['ada']}", headers=tokens["chioma"])
    body = client.get("/me/followers", headers=tokens["ada"]).json()
    assert body["count"] == 2
    assert [p["name"] for p in body["people"]] == ["Chioma", "Tunde"]
    assert set(body["people"][0]) == {"id", "name", "character"}
    assert "@" not in client.get("/me/followers", headers=tokens["ada"]).text


def test_others_see_counts_not_names(env):
    client, tokens, ids, _ = env
    client.put(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    res = client.get(f"/follows/{ids['ada']}")  # signed out
    assert res.json() == {"user_id": ids["ada"], "followers": 1, "following": 0, "you_follow": False, "friends": False}
    assert "Tunde" not in res.text
    res = client.get(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    assert res.json()["you_follow"] is True


def test_blocked_players_drop_out_of_lists_and_counts(env):
    client, tokens, ids, Session = env
    for who in ("tunde", "musa"):
        client.put(f"/follows/{ids['ada']}", headers=tokens[who])
        client.put(f"/follows/{ids[who]}", headers=tokens["ada"])
    block(Session, ids["musa"])
    assert [p["name"] for p in client.get("/me/friends", headers=tokens["ada"]).json()["people"]] == ["Tunde"]
    with Session() as db:
        assert service.counts(db, ids["ada"]) == {"followers": 1, "following": 1, "friends": 1}
        assert service.friends_of(db, ids["ada"]) == {ids["tunde"]}


def test_daily_follow_limit(env):
    client, tokens, ids, Session = env
    now = datetime.now(timezone.utc)
    with Session() as db:
        extra = [User(email=f"p{i}@example.com", name=f"P{i}") for i in range(service.DAILY_FOLLOW_LIMIT + 1)]
        db.add_all(extra)
        db.flush()
        # One follow made two days ago doesn't count; the rest were made in the last hour.
        db.add(Follow(follower_id=ids["ada"], target_user_id=extra[0].id, created_at=now - timedelta(days=2)))
        for u in extra[1:service.DAILY_FOLLOW_LIMIT]:
            db.add(Follow(follower_id=ids["ada"], target_user_id=u.id, created_at=now - timedelta(hours=1)))
        db.commit()
        last = extra[-1].id
    assert client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"]).status_code == 200  # number 100 today
    res = client.put(f"/follows/{last}", headers=tokens["ada"])
    assert res.status_code == 429 and res.json()["errors"][0]["code"] == "follow_limit"


def test_invite_link_makes_friends(env, heard):
    client, tokens, ids, _ = env
    invite = client.post("/friend-invites", headers=tokens["ada"]).json()
    assert invite["path"] == f"/f/{invite['code']}"
    assert client.post("/friend-invites", headers=tokens["ada"]).json()["code"] == invite["code"]  # same link

    preview = client.get(f"/friend-invites/{invite['code']}")  # signed out
    assert preview.status_code == 200 and preview.json()["inviter"]["name"] == "Ada"
    assert "@" not in preview.text

    res = client.post(f"/friend-invites/{invite['code']}/accept", headers=tokens["tunde"])
    assert res.status_code == 200 and res.json()["friend"]["name"] == "Ada"
    assert client.get("/me/friends", headers=tokens["ada"]).json()["people"][0]["name"] == "Tunde"
    assert sorted(heard) == sorted([(ids["tunde"], ids["ada"]), (ids["ada"], ids["tunde"])])

    # The same link works for a second friend, and accepting twice changes nothing.
    assert client.post(f"/friend-invites/{invite['code']}/accept", headers=tokens["chioma"]).status_code == 200
    assert client.post(f"/friend-invites/{invite['code']}/accept", headers=tokens["tunde"]).status_code == 200
    assert client.get("/me/friends", headers=tokens["ada"]).json()["count"] == 2
    assert len(heard) == 4


def test_accepting_completes_a_one_way_follow(env, heard):
    client, tokens, ids, _ = env
    client.put(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    code = client.post("/friend-invites", headers=tokens["ada"]).json()["code"]
    client.post(f"/friend-invites/{code}/accept", headers=tokens["tunde"])
    assert client.get(f"/follows/{ids['ada']}", headers=tokens["tunde"]).json()["friends"] is True
    assert heard == [(ids["tunde"], ids["ada"]), (ids["ada"], ids["tunde"])]


def test_cant_accept_your_own_invite(env):
    client, tokens, _, _ = env
    code = client.post("/friend-invites", headers=tokens["ada"]).json()["code"]
    res = client.post(f"/friend-invites/{code}/accept", headers=tokens["ada"])
    assert res.status_code == 400 and res.json()["errors"][0]["code"] == "own_invite"


def test_unknown_expired_or_blocked_invites_are_refused(env):
    client, tokens, ids, Session = env
    assert client.get("/friend-invites/nope").status_code == 404
    assert client.post("/friend-invites/nope/accept", headers=tokens["tunde"]).status_code == 404

    long_ago = datetime.now(timezone.utc) - timedelta(days=service.INVITE_DAYS + 1)
    with Session() as db:
        old = service.make_invite(db, db.get(User, ids["ada"]), at=long_ago)
        old_code = old.code
    res = client.post(f"/friend-invites/{old_code}/accept", headers=tokens["tunde"])
    assert res.status_code == 404 and res.json()["errors"][0]["code"] == "invite_not_found"
    assert client.post("/friend-invites", headers=tokens["ada"]).json()["code"] != old_code  # a fresh link

    code = client.post("/friend-invites", headers=tokens["musa"]).json()["code"]
    block(Session, ids["musa"])
    assert client.get(f"/friend-invites/{code}").status_code == 404


def test_an_invite_nearly_out_of_time_is_replaced(env):
    client, tokens, ids, Session = env
    nearly = datetime.now(timezone.utc) - timedelta(days=service.INVITE_DAYS) + timedelta(hours=12)
    with Session() as db:
        code = service.make_invite(db, db.get(User, ids["ada"]), at=nearly).code
    assert client.get(f"/friend-invites/{code}").status_code == 200  # still works
    assert client.post("/friend-invites", headers=tokens["ada"]).json()["code"] != code  # but a new one is handed out
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(FriendInvite)) == 2


def test_contract_for_other_features(env):
    client, tokens, ids, Session = env
    client.put(f"/follows/{ids['tunde']}", headers=tokens["ada"])
    client.put(f"/follows/{ids['ada']}", headers=tokens["tunde"])
    client.put(f"/follows/{ids['ada']}", headers=tokens["chioma"])
    with Session() as db:
        assert service.following_of(db, ids["ada"]) == {ids["tunde"]}
        assert service.followers_of(db, ids["ada"]) == {ids["tunde"], ids["chioma"]}
        assert service.friends_of(db, ids["ada"]) == {ids["tunde"]}
        assert service.is_friend(db, ids["ada"], ids["tunde"]) is True
        assert service.is_friend(db, ids["ada"], ids["chioma"]) is False
        assert service.is_friend(db, ids["ada"], ids["ada"]) is False
        assert service.counts(db, ids["ada"]) == {"followers": 2, "following": 1, "friends": 1}
