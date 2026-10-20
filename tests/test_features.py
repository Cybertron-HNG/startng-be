"""Phase 0 scaffolding: feature flags, the feature registry, events, startng.json sections, admin permissions."""
import json

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app import admin, events, features, flags, manifest
from app.config import settings
from app.leaderboard import SchemaError
from app.main import app
from tests.test_publish import env, make_zip  # noqa: F401  (the publish fixture)

# ---- flags


def test_flags(monkeypatch):
    monkeypatch.setattr(settings, "features", "")
    assert not flags.is_on("wardrobe")
    monkeypatch.setattr(settings, "features", " Wardrobe , profiles")
    assert flags.is_on("wardrobe") and flags.is_on("profiles") and not flags.is_on("leagues")
    monkeypatch.setattr(settings, "features", "*")
    assert flags.is_on("anything")
    assert TestClient(app).get("/features").json() == ["*"]


def test_a_feature_route_is_404_while_its_flag_is_off(monkeypatch):
    router = APIRouter(prefix="/thing")
    router.get("")(lambda: {"ok": True})
    test_app = FastAPI()
    test_app.include_router(router, dependencies=[Depends(flags.require("thing"))])  # how main.py mounts features
    client = TestClient(test_app)
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/thing").status_code == 404
    monkeypatch.setattr(settings, "features", "thing")
    assert client.get("/thing").json() == {"ok": True}


# ---- the feature registry


def test_the_template_feature_loads_and_registers_its_permissions():
    [template] = features.load(["_template"])
    assert template.NAME == "example" and template.router.prefix == "/example"
    assert admin.PERMISSIONS["example.manage"] == "Manage examples"
    assert features.load(["_template"]) == [template]  # loading twice is fine


def test_two_features_cant_claim_the_same_admin_permission():
    admin.register_permissions({"phase0.test": "Test"})
    admin.register_permissions({"phase0.test": "Test"})  # the same one again is fine
    with pytest.raises(ValueError):
        admin.register_permissions({"phase0.test": "Something else"})
    del admin.PERMISSIONS["phase0.test"]


# ---- events


def test_events_reach_subscribers_and_a_failing_one_is_skipped():
    seen = []

    def broken(payload):
        raise RuntimeError("boom")

    def good(payload):
        seen.append(payload["game"])

    events.subscribe("game_unhealthy")(broken)
    events.subscribe("game_unhealthy")(good)
    try:
        events.publish("game_unhealthy", {"game": "g1", "reason": "blank page"})
        assert seen == ["g1"]
    finally:
        events.unsubscribe("game_unhealthy", broken)
        events.unsubscribe("game_unhealthy", good)


def test_events_must_be_listed_and_carry_their_payload():
    with pytest.raises(KeyError):
        events.publish("not_an_event", {})
    with pytest.raises(KeyError):
        events.subscribe("not_an_event")
    with pytest.raises(KeyError):
        events.publish("game_published", {"game": "g1"})  # owner_id missing


def test_publishing_a_game_publishes_game_published_then_game_updated(env):  # noqa: F811
    client, _storage, tokens, _archives = env
    got = []
    handler = events.subscribe("game_published")(lambda p: got.append(("published", p)))
    handler2 = events.subscribe("game_updated")(lambda p: got.append(("updated", p)))
    try:
        for _ in range(2):
            assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"]).status_code == 200
    finally:
        events.unsubscribe("game_published", handler)
        events.unsubscribe("game_updated", handler2)
    assert [k for k, _ in got] == ["published", "updated"]
    assert got[0][1]["game"] == "lane-dash" and got[1][1]["auto"] is False


# ---- startng.json sections


def test_sections_are_parsed_by_their_owner_and_saved_on_publish(env):  # noqa: F811
    client, _storage, tokens, archives = env
    saved = {}

    def parse(value):
        if value not in ("Puzzle", "Racing"):
            raise SchemaError('category must be "Puzzle" or "Racing"')
        return value

    manifest.register_section("phase0_category", parse, lambda db, game, value: saved.update({game.name: value}))
    try:
        assert manifest.parse_sections(b'{"phase0_category": "Puzzle", "unknown": 1}') == {"phase0_category": "Puzzle"}
        files = {"index.html": "<h1>hi</h1>", "startng.json": json.dumps({"phase0_category": "Racing", "description": "Go fast."})}
        archives["zip"] = make_zip(files)
        assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"]).status_code == 200
        assert saved == {"lane-dash": "Racing"}
        # a bad section refuses the publish, with its owner's message
        files["startng.json"] = json.dumps({"phase0_category": "Chess"})
        archives["zip"] = make_zip(files)
        res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
        assert res.status_code == 422 and "Puzzle" in res.json()["detail"]
        with pytest.raises(ValueError):
            manifest.register_section("phase0_category", lambda v: v)  # another owner can't take it
    finally:
        del manifest.SECTIONS["phase0_category"]


def test_the_built_in_sections_behave_as_before():
    assert manifest.parse_sections(b'{"description": "  Dodge \\n the lava. "}') == {"description": "Dodge the lava."}
    assert manifest.parse_sections(b'{"description": 5}') == {"description": None}
    with pytest.raises(SchemaError):
        manifest.parse_sections(b"not json")
    with pytest.raises(SchemaError):
        manifest.parse_sections(b'{"leaderboard": {"stats": []}}')


# ---- hiding games from a feature


def test_a_feature_can_hide_a_game_with_its_own_reason():
    from app.models import Game

    class Store:
        offline = set()

        def set_hidden(self, name, hidden):
            (self.offline.add if hidden else self.offline.discard)(name)

    class FakeDb:
        def commit(self):
            pass

    admin.register_hide_reason("phase0_reason")
    assert admin.HIDE_REASONS.index("phase0_reason") == admin.HIDE_REASONS.index(admin.FLAGGED) - 1
    game, store = Game(name="g1"), Store()
    try:
        admin.hide(game, admin.REPORTED)
        admin.hide(game, "phase0_reason")  # stronger than a report
        assert game.hidden == "phase0_reason"
        admin.hide(game, admin.REPORTED)  # a weaker reason doesn't replace it
        assert game.hidden == "phase0_reason"
        admin.show_game(FakeDb(), store, game)
        assert game.hidden is None and store.offline == set()
    finally:
        admin.HIDE_REASONS.remove("phase0_reason")
