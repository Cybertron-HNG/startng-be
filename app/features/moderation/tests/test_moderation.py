import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.moderation import service
from app.main import app
from app.models import Game, IndieGame, User
from app.security import create_token
from tests.test_publish import env, make_zip  # noqa: F401  (the publish fixture)

CHARACTER = {"primaryColor": "#a55eea", "secondaryColor": "#222222", "skinColor": "#8d5524", "hairColor": "#111111",
             "body": "male", "hair": "short", "hat": "none", "accessory": "none"}


@pytest.fixture(autouse=True)
def fresh_lists():
    service.forget_cache()
    yield
    service.forget_cache()


@pytest.fixture
def db_env(monkeypatch):
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "moderation")
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
    with Session() as db:
        boss = User(email="boss@example.com", name="Boss", google_sub="g1")
        ada = User(email="ada@example.com", name="Ada", google_sub="g2")
        db.add_all([boss, ada])
        db.commit()
        tokens = {"boss": {"Authorization": f"Bearer {create_token(boss.id)}"}, "ada": {"Authorization": f"Bearer {create_token(ada.id)}"}}
    yield TestClient(app), tokens, Session
    app.dependency_overrides.clear()


# ---- matching (the built-in lists; no admin words)


@pytest.mark.parametrize("text", [
    "Lane Dash", "Assassin Run", "Classic Racer", "Scunthorpe United", "Therapist Simulator", "Spicy Noodles",
    "Torpedo Strike", "Flame Retardant", "Ode to Joy", "Pass the Class", "Cockpit Hero", "Grass Mass", "Shitake Farm",
    "A Bass Odyssey", "2048", "", None,
])
def test_normal_names_pass(text, db_env):
    _, _, Session = db_env
    with Session() as db:
        assert service.check_text(text, db) is None


@pytest.mark.parametrize("text, word", [
    ("Fuck Racer", "fuck"), ("fuckgame", "fuck"), ("Sh1t Show", "shit"), ("$hit", "shit"), ("s.h.i.t", "shit"),
    ("shiiit", "shit"), ("f u c k", "fuck"), ("Big ASSES", "ass"), ("rapists", "rapist"), ("Ashawo Run", "ashawo"),
    ("Pórn Hub", "porn"), ("Mumu Game", "mumu"),
])
def test_offensive_names_are_caught(text, word, db_env):
    _, _, Session = db_env
    with Session() as db:
        assert service.check_text(text, db) == word


# ---- admins' own words


def test_admins_add_and_remove_words(db_env):
    client, tokens, _ = db_env
    url = "/admin/moderation/words"

    def check(text):
        return client.post("/admin/moderation/try", json={"text": text}, headers=tokens["boss"]).json()

    assert check("Mumu Racer") == {"ok": False, "word": "mumu"}
    # allow a built-in word, block a new one (strong: inside other words too)
    allow = client.post(url, json={"word": "Mumu", "kind": "allow"}, headers=tokens["boss"])
    assert allow.status_code == 201 and allow.json()["word"] == "mumu"
    assert client.post(url, json={"word": "zorbo", "kind": "block", "strong": True}, headers=tokens["boss"]).status_code == 201
    assert check("Mumu Racer")["ok"] and check("Megazorbo")["word"] == "zorbo"
    assert client.post(url, json={"word": "zorbo", "kind": "block"}, headers=tokens["boss"]).status_code == 409
    assert client.post(url, json={"word": "two words", "kind": "block"}, headers=tokens["boss"]).status_code == 422
    words = client.get(url, headers=tokens["boss"]).json()
    assert [w["word"] for w in words if not w["built_in"]] == ["zorbo", "mumu"]
    client.delete(f"{url}/{words[0]['id']}", headers=tokens["boss"])
    assert check("Megazorbo")["ok"]


def test_only_admins_with_the_permission_and_only_with_the_flag(db_env, monkeypatch):
    client, tokens, _ = db_env
    assert client.get("/admin/moderation/words", headers=tokens["ada"]).status_code == 404  # not an admin
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/admin/moderation/words", headers=tokens["boss"]).status_code == 404  # flag off


def test_flagged_lists_names_already_on_start_ng(db_env):
    client, tokens, Session = db_env
    with Session() as db:
        ada = db.query(User).filter_by(email="ada@example.com").one()
        ada.character = {"name": "Sh1tLord", "style": CHARACTER}
        db.add(Game(name="fuck-race", owner_id=ada.id, repo_url="https://github.com/ada/x", description="A fine game"))
        db.add(Game(name="lane-dash", owner_id=ada.id, repo_url="https://github.com/ada/y", description="Avoid the ashawo"))
        db.add(IndieGame(owner_id=ada.id, name="Clean Name", url="https://ada.itch.io/z", description="Totally porn free", cover=b""))
        db.commit()
    got = {(f["kind"], f["target"], f["word"]) for f in client.get("/admin/moderation/flagged", headers=tokens["boss"]).json()}
    assert got == {("game", "fuck-race", "fuck"), ("game_description", "lane-dash", "ashawo"), ("character", "2", "shit"), ("creators_listing", "1", "porn")}


# ---- start.ng's own names, while the flag is on


def test_character_names(db_env, monkeypatch):
    client, tokens, _ = db_env
    bad = client.put("/auth/me/character", json={"name": "Fuk Boi", "style": CHARACTER}, headers=tokens["ada"])
    assert bad.status_code == 422 and bad.json()["errors"][0]["code"] == "text_not_allowed"
    assert client.put("/auth/me/character", json={"name": "Ada", "style": CHARACTER}, headers=tokens["ada"]).status_code == 200
    monkeypatch.setattr(settings, "features", "")  # flag off: not checked
    assert client.put("/auth/me/character", json={"name": "Fuk Boi", "style": CHARACTER}, headers=tokens["ada"]).status_code == 200


def test_creators_listings(db_env):
    client, tokens, _ = db_env
    res = client.post("/indie", json={"name": "Shit Game", "url": "https://ada.itch.io/g", "description": "A fast racing game with three tracks and a boss at the end.", "cover": "x"}, headers=tokens["ada"])
    assert res.status_code == 422 and res.json()["errors"][0]["code"] == "text_not_allowed"


def test_publishing(env, monkeypatch):  # noqa: F811
    client, _storage, tokens, archives = env
    monkeypatch.setattr(settings, "features", "moderation")
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Fuck-Race"}, headers=tokens["ada"])
    assert res.status_code == 422 and res.json()["errors"][0]["code"] == "text_not_allowed"
    archives["zip"] = make_zip({"index.html": "<h1>hi</h1>", "startng.json": json.dumps({"description": "Kill the ashawo"})})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert res.status_code == 422 and res.json()["errors"][0]["file"] == "startng.json"
    # a description start.ng found in the page itself is just left out, not refused
    archives["zip"] = make_zip({"index.html": '<meta name="description" content="A shit game"><h1>hi</h1>'})
    assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"]).status_code == 200
    assert client.get("/games").json()[0]["description"] is None
    monkeypatch.setattr(settings, "features", "")  # flag off: published as before
    archives["zip"] = make_zip({"index.html": "<h1>hi</h1>"})
    assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/Fuck-Race"}, headers=tokens["ada"]).status_code == 200
