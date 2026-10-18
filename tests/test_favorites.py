from app.routers import favorites
from tests.test_publish import env  # noqa: F401 (fixture)


def test_must_be_signed_in(env):
    client, *_ = env
    assert client.get("/favorites").status_code == 401
    assert client.put("/favorites/g:moon-garden").status_code == 401


def test_add_list_remove_newest_first(env):
    client, _, tokens, _ = env
    ada = tokens["ada"]
    assert client.get("/favorites", headers=ada).json() == []
    assert client.put("/favorites/g:moon-garden", headers=ada).json() == ["g:moon-garden"]
    assert client.put("/favorites/i:7", headers=ada).json() == ["i:7", "g:moon-garden"]
    assert client.put("/favorites/i:7", headers=ada).json() == ["i:7", "g:moon-garden"]  # again: no duplicate
    assert client.delete("/favorites/i:7", headers=ada).json() == ["g:moon-garden"]
    assert client.delete("/favorites/i:7", headers=ada).json() == ["g:moon-garden"]  # already gone: fine


def test_each_player_has_their_own(env):
    client, _, tokens, _ = env
    client.put("/favorites/g:moon-garden", headers=tokens["ada"])
    assert client.get("/favorites", headers=tokens["tunde"]).json() == []
    client.delete("/favorites/g:moon-garden", headers=tokens["tunde"])  # can't remove Ada's
    assert client.get("/favorites", headers=tokens["ada"]).json() == ["g:moon-garden"]


def test_sync_merges_a_devices_favorites(env):
    client, _, tokens, _ = env
    ada = tokens["ada"]
    client.put("/favorites/g:lane-dash", headers=ada)
    res = client.post("/favorites/sync", json={"keys": ["g:moon-garden", "g:lane-dash", "i:3", "i:3"]}, headers=ada)
    assert res.status_code == 200
    assert sorted(res.json()) == ["g:lane-dash", "g:moon-garden", "i:3"]
    assert res.json()[0] == "g:moon-garden"  # the device's first is the newest


def test_bad_keys_are_refused(env):
    client, _, tokens, _ = env
    ada = tokens["ada"]
    for bad in ["x:1", "g:", "g:Has Caps", "i:abc", "g:" + "a" * 41]:
        assert client.put(f"/favorites/{bad}", headers=ada).status_code == 422, bad
    assert client.post("/favorites/sync", json={"keys": ["g:moon-garden\n"]}, headers=ada).status_code == 422  # no trailing newline
    assert client.post("/favorites/sync", json={"keys": ["g:ok", "nope"]}, headers=ada).status_code == 422
    assert client.get("/favorites", headers=ada).json() == []  # nothing half-saved


def test_limit(env, monkeypatch):
    client, _, tokens, _ = env
    monkeypatch.setattr(favorites, "MAX_FAVORITES", 2)
    ada = tokens["ada"]
    client.put("/favorites/i:1", headers=ada)
    client.put("/favorites/i:2", headers=ada)
    res = client.put("/favorites/i:3", headers=ada)
    assert res.status_code == 409
    assert len(client.get("/favorites", headers=ada).json()) == 2
