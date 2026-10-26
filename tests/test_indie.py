import base64
import io

import pytest
from PIL import Image

from app import covers
from app.routers import indie
from tests.test_covers import picture
from tests.test_publish import env  # noqa: F401 (fixture)


def data_url(data: bytes, kind: str = "png") -> str:
    return f"data:image/{kind};base64," + base64.b64encode(data).decode()


def game(**over):
    return {
        "name": "Moon Garden",
        "url": "https://ada.itch.io/moon-garden",
        "description": "Grow glowing plants on the moon before the sun comes up.",
        "cover": data_url(picture()),
        **over,
    }


@pytest.fixture(autouse=True)
def fresh_limit():
    indie._submits.clear()


def test_submit_list_and_cover(env):
    client, _, tokens, _ = env
    res = client.post("/indie", json=game(), headers=tokens["ada"])
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["name"], body["by"], body["mine"]) == ("Moon Garden", "Ada", True)
    assert body["cover_path"] == f"/indie/{body['id']}/cover.webp"

    listed = client.get("/indie").json()
    assert [(g["name"], g["mine"]) for g in listed] == [("Moon Garden", False)]  # not signed in

    cover = client.get(body["cover_path"])
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/webp"
    assert "immutable" in cover.headers["cache-control"]
    assert Image.open(io.BytesIO(cover.content)).size == covers.SIZE


def test_newest_first_and_limit(env):
    client, _, tokens, _ = env
    for i in range(5):
        client.post("/indie", json=game(name=f"Game {i}", url=f"https://ada.itch.io/g{i}"), headers=tokens["ada"])
    names = [g["name"] for g in client.get("/indie", params={"limit": 4}).json()]
    assert names == ["Game 4", "Game 3", "Game 2", "Game 1"]


def test_must_be_signed_in(env):
    client, *_ = env
    assert client.post("/indie", json=game()).status_code == 401


@pytest.mark.parametrize("over, code", [
    ({"name": "x"}, "bad_name"),
    ({"description": "short"}, "bad_description"),
    ({"url": "http://ada.itch.io/game"}, "bad_url"),
    ({"url": "javascript:alert(1)"}, "bad_url"),
    ({"url": "https://localhost/game"}, "bad_url"),
    ({"url": "https://user:pass@ada.itch.io/game"}, "bad_url"),
    ({"cover": ""}, "bad_cover"),
    ({"cover": "data:image/svg+xml;base64,PHN2Zy8+"}, "bad_cover"),
    ({"cover": data_url(b"<script>alert(1)</script>")}, "bad_cover"),
    ({"cover": "data:image/png;base64,not base64!"}, "bad_cover"),
])
def test_bad_submissions_are_refused(env, over, code):
    client, _, tokens, _ = env
    res = client.post("/indie", json=game(**over), headers=tokens["ada"])
    assert res.status_code == 422, res.text
    assert res.json()["errors"][0]["code"] == code
    assert client.get("/indie").json() == []


def test_big_cover_is_refused(env, monkeypatch):
    client, _, tokens, _ = env
    monkeypatch.setattr(covers, "MAX_UPLOAD_BYTES", 100)
    res = client.post("/indie", json=game(), headers=tokens["ada"])
    assert res.status_code == 413


def test_same_link_twice_is_refused(env):
    client, _, tokens, _ = env
    client.post("/indie", json=game(), headers=tokens["ada"])
    res = client.post("/indie", json=game(name="Copy"), headers=tokens["tunde"])
    assert res.status_code == 409


def test_text_is_cleaned(env):
    client, _, tokens, _ = env
    body = client.post("/indie", json=game(name="  Moon\x00 Garden  "), headers=tokens["ada"]).json()
    assert body["name"] == "Moon Garden"


def test_only_the_creator_can_remove_it(env):
    client, _, tokens, _ = env
    gid = client.post("/indie", json=game(), headers=tokens["ada"]).json()["id"]
    assert client.delete(f"/indie/{gid}", headers=tokens["tunde"]).status_code == 403
    assert client.delete(f"/indie/{gid}", headers=tokens["ada"]).status_code == 204
    assert client.get("/indie").json() == []
    assert client.get(f"/indie/{gid}/cover.webp").status_code == 404


def test_too_many_submissions(env, monkeypatch):
    client, _, tokens, _ = env
    monkeypatch.setattr(indie, "SUBMITS_PER_HOUR", 2)
    for i in range(2):
        assert client.post("/indie", json=game(url=f"https://ada.itch.io/g{i}"), headers=tokens["ada"]).status_code == 201
    assert client.post("/indie", json=game(url="https://ada.itch.io/g9"), headers=tokens["ada"]).status_code == 429


def test_most_clicked_first(env):
    client, _, tokens, _ = env
    ids = [client.post("/indie", json=game(name=f"Game {i}", url=f"https://ada.itch.io/g{i}"), headers=tokens["ada"]).json()["id"] for i in range(3)]
    for ip in ["10.0.0.1", "10.0.0.2", "10.0.0.3"]:
        client.post(f"/indie/{ids[0]}/plays", headers={"x-forwarded-for": ip})
    client.post(f"/indie/{ids[1]}/plays", headers={"x-forwarded-for": "10.0.0.1"})
    client.post(f"/indie/{ids[1]}/plays", headers={"x-forwarded-for": "10.0.0.1"})  # same person again: counts once
    popular = client.get("/indie", params={"sort": "popular", "limit": 3}).json()
    assert [(g["name"], g["plays"]) for g in popular] == [("Game 0", 3), ("Game 1", 1), ("Game 2", 0)]
    assert client.post("/indie/999/plays").status_code == 404


def test_changing_the_user_agent_does_not_inflate_indie_plays(env):
    client, _, tokens, _ = env
    gid = client.post("/indie", json=game(), headers=tokens["ada"]).json()["id"]
    for ua in ["a", "b", "c"]:
        client.post(f"/indie/{gid}/plays", headers={"user-agent": ua})
    assert client.get("/indie").json()[0]["plays"] == 1


def test_indie_plays_have_their_own_budget_per_address(env, monkeypatch):
    client, _, tokens, _ = env
    from app.routers import indie as indie_router
    gid = client.post("/indie", json=game(), headers=tokens["ada"]).json()["id"]
    monkeypatch.setattr(indie_router, "PLAYS_PER_HOUR", 2)
    indie_router._plays.clear()
    assert [client.post(f"/indie/{gid}/plays").status_code for _ in range(3)] == [204, 204, 429]
    indie_router._plays.clear()


def test_the_listing_does_not_load_covers_and_respects_the_limit(env):
    client, _, tokens, _ = env
    for i in range(3):
        client.post("/indie", json=game(name=f"Game {i}", url=f"https://ada.itch.io/g{i}"), headers=tokens["ada"])
    newest = client.get("/indie", params={"limit": 2}).json()
    assert [g["name"] for g in newest] == ["Game 2", "Game 1"]
    assert all(g["by"] for g in newest)


def test_two_requests_for_the_same_link_get_409_not_500(env, monkeypatch):
    client, _, tokens, _ = env
    from sqlalchemy.orm import Session
    assert client.post("/indie", json=game(), headers=tokens["ada"]).status_code == 201
    # Make the "already listed?" check miss, as it does when two requests run at once:
    # only the database's unique constraint is left to stop the second one.
    real = Session.scalar
    monkeypatch.setattr(Session, "scalar", lambda self, stmt, *a, **k: None if "indie_games.url" in str(stmt) else real(self, stmt, *a, **k))
    res = client.post("/indie", json=game(), headers=tokens["ada"])
    assert res.status_code == 409
    assert "already listed" in res.text


# ---- categories (the tags feature): picked on the form, shown on the game's own page only

def test_categories_are_ignored_while_the_tags_feature_is_off(env):
    client, _, tokens, _ = env
    res = client.post("/indie", json=game(categories=["not-a-category"]), headers=tokens["ada"])
    assert res.status_code == 201 and client.get(f"/indie/{res.json()['id']}").json()["categories"] == []


def test_categories_are_saved_and_shown_on_the_game_page(env, monkeypatch):
    from app.config import settings

    client, _, tokens, _ = env
    monkeypatch.setattr(settings, "features", "tags")
    res = client.post("/indie", json=game(categories=["Brainy games", "cosy-and-relaxing"], other_category="Gardening"), headers=tokens["ada"])
    assert res.status_code == 201, res.text
    assert res.json()["categories"] == ["Brainy games", "Cosy and relaxing"]
    one = client.get(f"/indie/{res.json()['id']}").json()
    assert one["categories"] == ["Brainy games", "Cosy and relaxing"]
    assert all(g["categories"] == [] for g in client.get("/indie").json())  # not in the list on the home page


def test_a_category_that_does_not_exist_is_refused_with_the_list(env, monkeypatch):
    from app.config import settings

    client, _, tokens, _ = env
    monkeypatch.setattr(settings, "features", "tags")
    res = client.post("/indie", json=game(categories=["racing"]), headers=tokens["ada"])
    assert res.status_code == 422 and res.json()["errors"][0]["code"] == "bad_categories" and "endless-runners" in res.json()["errors"][0]["message"]
    assert client.post("/indie", json=game(categories=["archery"] * 1 + ["survival", "lagos", "educational", "3d-games", "brainy-games"]), headers=tokens["ada"]).status_code == 422
