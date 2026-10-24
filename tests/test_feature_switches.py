from app import db as db_module
from app import features, flags
from app.config import settings
from tests.test_admin import env  # noqa: F401  (admins, a super admin "boss", and a test database)


def use_switches(monkeypatch, Session, env_features=""):
    """Let the flags read the admin switches from the test database."""
    monkeypatch.setattr(settings, "feature_switches", True)
    monkeypatch.setattr(settings, "features", env_features)
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    flags.forget()


def test_only_super_admins_see_and_use_the_switches(env, monkeypatch):
    client, tokens, ids, Session, _ = env
    use_switches(monkeypatch, Session)
    assert client.get("/admin/features").status_code == 401
    assert client.get("/admin/features", headers=tokens["player"]).status_code == 404  # not an admin at all
    assert client.put("/admin/features/wardrobe", json={"enabled": True}, headers=tokens["player"]).status_code == 404
    mod = client.post("/admin/roles", json={"name": "Mod", "permissions": ["reports.view"]}, headers=tokens["boss"]).json()
    client.post("/admin/admins", json={"email": "mod@example.com", "role_id": mod["id"]}, headers=tokens["boss"])
    assert client.get("/admin/features", headers=tokens["mod"]).status_code == 403  # an admin, but not a super admin
    assert client.put("/admin/features/wardrobe", json={"enabled": True}, headers=tokens["mod"]).status_code == 403


def test_the_list_shows_every_feature_with_where_its_state_comes_from(env, monkeypatch):
    client, tokens, ids, Session, _ = env
    use_switches(monkeypatch, Session, "wardrobe")
    rows = {r["name"]: r for r in client.get("/admin/features", headers=tokens["boss"]).json()}
    assert set(rows) == set(features.FEATURES)
    assert rows["wardrobe"]["enabled"] and rows["wardrobe"]["source"] == "server" and rows["wardrobe"]["server_on"]
    assert not rows["profiles"]["enabled"] and rows["profiles"]["description"]
    assert rows["moderation"]["caution"]  # the tab asks before switching these on
    assert rows["profiles"]["caution"] is None


def test_switching_a_feature_on_and_off_takes_effect_at_once(env, monkeypatch):
    client, tokens, ids, Session, _ = env
    use_switches(monkeypatch, Session)
    boss = tokens["boss"]
    assert not flags.is_on("profiles") and "profiles" not in client.get("/features").json()
    rows = client.put("/admin/features/profiles", json={"enabled": True}, headers=boss).json()
    row = next(r for r in rows if r["name"] == "profiles")
    assert row["enabled"] and row["source"] == "admin" and row["changed_by"] == "boss@example.com" and not row["server_on"]
    assert flags.is_on("profiles") and "profiles" in client.get("/features").json()
    client.put("/admin/features/profiles", json={"enabled": False}, headers=boss)
    assert not flags.is_on("profiles") and "profiles" not in client.get("/features").json()


def test_a_switch_beats_the_server_setting_both_ways(env, monkeypatch):
    client, tokens, ids, Session, _ = env
    use_switches(monkeypatch, Session, "wardrobe,leagues")
    boss = tokens["boss"]
    assert flags.is_on("wardrobe")
    client.put("/admin/features/wardrobe", json={"enabled": False}, headers=boss)  # off although the setting says on
    assert not flags.is_on("wardrobe") and flags.is_on("leagues")
    assert sorted(client.get("/features").json()) == ["leagues"]
    client.put("/admin/features/wardrobe", json={"enabled": None}, headers=boss)  # back to the setting
    assert flags.is_on("wardrobe")
    row = next(r for r in client.get("/admin/features", headers=boss).json() if r["name"] == "wardrobe")
    assert row["source"] == "server" and row["changed_by"] is None


def test_switching_one_off_when_everything_is_on_lists_the_rest(env, monkeypatch):
    client, tokens, ids, Session, _ = env
    use_switches(monkeypatch, Session, "*")
    assert client.get("/features").json() == ["*"]
    client.put("/admin/features/wardrobe", json={"enabled": False}, headers=tokens["boss"])
    listed = client.get("/features").json()
    assert "wardrobe" not in listed and "*" not in listed and set(listed) == set(features.FEATURES) - {"wardrobe"}
    assert not flags.is_on("wardrobe") and flags.is_on("leagues")


def test_unknown_features_are_refused_and_changes_are_logged(env, monkeypatch):
    client, tokens, ids, Session, _ = env
    use_switches(monkeypatch, Session)
    boss = tokens["boss"]
    assert client.put("/admin/features/nope", json={"enabled": True}, headers=boss).status_code == 404
    client.put("/admin/features/profiles", json={"enabled": True}, headers=boss)
    client.put("/admin/features/profiles", json={"enabled": None}, headers=boss)
    actions = [row["action"] for row in client.get("/admin/log", headers=boss).json()]
    assert "feature.on" in actions and "feature.reset" in actions


def test_without_the_table_the_setting_alone_decides(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    empty = sessionmaker(bind=create_engine("sqlite://"))  # a database with no feature_switches table
    use_switches(monkeypatch, empty, "wardrobe")
    assert flags.is_on("wardrobe") and not flags.is_on("profiles")
