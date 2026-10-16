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
