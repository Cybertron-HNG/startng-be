import pytest

from app import usernames
from app.db import get_db
from app.models import User
from tests.test_publish import env  # noqa: F401 (fixture)


def _db(client):
    return next(client.app.dependency_overrides[get_db]())


def test_everyone_gets_a_default_and_it_is_unique(env):
    client, _, tokens, _ = env
    a = client.get("/auth/me", headers=tokens["ada"]).json()["username"]
    t = client.get("/auth/me", headers=tokens["tunde"]).json()["username"]
    assert a == "ada" and t == "tunde"
    # a second Ada gets digits instead of the same name
    r = client.post("/auth/register", json={"email": "ada2@example.com", "password": "password123", "name": "Ada"})
    assert r.status_code == 201
    other = r.json()["user"]["username"]
    assert other != "ada" and other.startswith("ada") and usernames.problem(None, other) is None


def test_a_default_follows_the_rules_even_for_odd_names():
    db = None
    u = User(email="12345@example.com", name="!!")
    assert usernames.problem(db, usernames.base_for(u)) is None
    u2 = User(email="x@example.com", name="Ọlá Ṣeun")
    base = usernames.base_for(u2)
    assert usernames.PATTERN.match(base) and len(base) >= usernames.MIN


def test_change_username(env):
    client, _, tokens, _ = env
    ada = tokens["ada"]
    r = client.put("/auth/me/username", json={"username": "  @Ada_Plays "}, headers=ada)
    assert r.status_code == 200 and r.json()["username"] == "ada_plays"
    assert client.get("/auth/me", headers=ada).json()["username"] == "ada_plays"


def test_taken_names_are_refused_whatever_the_case(env):
    client, _, tokens, _ = env
    client.put("/auth/me/username", json={"username": "ada_plays"}, headers=tokens["ada"])
    r = client.put("/auth/me/username", json={"username": "ADA_Plays"}, headers=tokens["tunde"])
    assert r.status_code == 409
    # your own is fine to "change" to
    assert client.put("/auth/me/username", json={"username": "ada_plays"}, headers=tokens["ada"]).status_code == 200


@pytest.mark.parametrize("bad", ["ab", "a" * 25, "has space", "dash-ed", "12345", "admin", "émile"])
def test_invalid_names_are_refused(env, bad):
    client, _, tokens, _ = env
    assert client.put("/auth/me/username", json={"username": bad}, headers=tokens["ada"]).status_code == 422


def test_check_endpoint(env):
    client, _, tokens, _ = env
    assert client.get("/auth/usernames/fresh_name").json() == {"username": "fresh_name", "available": True, "reason": None}
    client.get("/auth/me", headers=tokens["ada"])  # makes hers
    taken = client.get("/auth/usernames/ADA").json()
    assert taken["available"] is False and "already" in taken["reason"]
    assert client.get("/auth/usernames/no").json()["available"] is False


def test_blocked_words_are_refused(env, monkeypatch):
    from app.features.moderation import service as moderation

    monkeypatch.setattr(moderation, "check_text", lambda text, db=None: "bad" if "badword" in text else None)
    client, _, tokens, _ = env
    assert client.put("/auth/me/username", json={"username": "badword_1"}, headers=tokens["ada"]).status_code == 422


def test_sign_in_without_one_still_works_and_creates_it(env):
    client, _, tokens, _ = env
    db = _db(client)
    assert db.query(User).filter(User.username.is_(None)).count() == 2  # the fixture's users have none yet
    assert client.get("/auth/me", headers=tokens["ada"]).json()["username"]
