import io

import pytest
from PIL import Image

from app import covers
from app.covers import CoverError, make_cover
from tests.test_publish import GAME, env, make_zip  # noqa: F401 (env is a fixture)


def picture(fmt: str = "PNG", size: tuple[int, int] = (1200, 900), mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


# --- Making the picture ---------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_makes_a_card_sized_webp(fmt):
    out = Image.open(io.BytesIO(make_cover(picture(fmt))))
    assert (out.format, out.size) == ("WEBP", covers.SIZE)


def test_keeps_transparency():
    assert Image.open(io.BytesIO(make_cover(picture(mode="RGBA")))).mode == "RGBA"


@pytest.mark.parametrize("data", [b"<svg onload=alert(1)/>", b"GIF89a....", b"not a picture", picture("GIF")])
def test_refuses_anything_else(data):
    with pytest.raises(CoverError, match="PNG, JPEG or WebP"):
        make_cover(data)


def test_refuses_huge_pictures(monkeypatch):
    monkeypatch.setattr(covers, "MAX_PIXELS", 100 * 100)
    with pytest.raises(CoverError, match="too big"):
        make_cover(picture(size=(200, 200)))


def test_finds_the_games_own_cover():
    assert covers.repo_cover(["index.html", "cover.jpg", "cover.png"]) == "cover.png"
    assert covers.repo_cover(["index.html", "img/cover.png"]) is None


# --- The API --------------------------------------------------------------------------


def publish(client, tokens, who="ada", name="lane-dash"):
    res = client.post("/games/publish", json={"repo_url": f"https://github.com/{who}/x", "name": name}, headers=tokens[who])
    assert res.status_code == 200, res.text


def cover_of(client, name="lane-dash"):
    return next(g["cover_url"] for g in client.get("/games").json() if g["name"] == name)


def test_games_have_no_cover_by_default(env):
    client, _, tokens, _ = env
    publish(client, tokens)
    assert cover_of(client) is None


def test_uses_the_games_own_cover_png(env):
    client, _, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": picture()})
    publish(client, tokens)
    assert cover_of(client).startswith("https://lane-dash.start.ng/cover.png?v=")


def test_owner_uploads_a_cover_and_it_shows_everywhere(env):
    client, storage, tokens, _ = env
    publish(client, tokens)
    res = client.put("/games/lane-dash/cover", content=picture("JPEG"), headers=tokens["ada"])
    assert res.status_code == 200, res.text
    url = res.json()["cover_url"]
    assert url.startswith(f"https://lane-dash.start.ng/{covers.COVER_FILE}?v=")
    assert cover_of(client) == url
    assert client.get("/games/mine", headers=tokens["ada"]).json()[0]["cover_url"] == url
    saved = Image.open(storage.root / "lane-dash" / covers.COVER_FILE)
    assert (saved.format, saved.size) == ("WEBP", covers.SIZE)


def test_uploaded_cover_wins_over_the_repo_and_survives_republishing(env):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": picture()})
    publish(client, tokens)
    client.put("/games/lane-dash/cover", content=picture(), headers=tokens["ada"])
    uploaded = (storage.root / "lane-dash" / covers.COVER_FILE).read_bytes()

    # A new version, even one with a file named like the uploaded cover, keeps it.
    archives["zip"] = make_zip({**GAME, "cover.png": picture(), covers.COVER_FILE: b"from the repo"})
    publish(client, tokens)
    assert covers.COVER_FILE in cover_of(client)
    assert (storage.root / "lane-dash" / covers.COVER_FILE).read_bytes() == uploaded


def test_removing_the_upload_goes_back_to_the_games_own_cover(env):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": picture()})
    publish(client, tokens)
    client.put("/games/lane-dash/cover", content=picture(), headers=tokens["ada"])
    res = client.delete("/games/lane-dash/cover", headers=tokens["ada"])
    assert res.status_code == 200
    assert "/cover.png?v=" in res.json()["cover_url"]
    assert not (storage.root / "lane-dash" / covers.COVER_FILE).exists()


def test_only_the_owner_can_change_the_cover(env):
    client, _, tokens, _ = env
    publish(client, tokens)
    assert client.put("/games/lane-dash/cover", content=picture()).status_code == 401
    assert client.put("/games/lane-dash/cover", content=picture(), headers=tokens["tunde"]).status_code == 403
    assert client.delete("/games/lane-dash/cover", headers=tokens["tunde"]).status_code == 403
    assert client.put("/games/nope/cover", content=picture(), headers=tokens["ada"]).status_code == 404
    assert cover_of(client) is None


def test_bad_uploads_are_refused(env, monkeypatch):
    client, _, tokens, _ = env
    publish(client, tokens)
    assert client.put("/games/lane-dash/cover", content=b"", headers=tokens["ada"]).status_code == 400
    assert client.put("/games/lane-dash/cover", content=b"<svg/>", headers=tokens["ada"]).status_code == 422
    monkeypatch.setattr(covers, "MAX_UPLOAD_BYTES", 1000)
    assert client.put("/games/lane-dash/cover", content=picture(), headers=tokens["ada"]).status_code == 413
    assert cover_of(client) is None


def test_hangout_has_seeded_cover_fallback():
    from app.models import Game
    from app.routers.games import _cover_url
    g = Game(name="hangout", cover=None)
    assert _cover_url(g) == "/hangout-cover.jpg"


def test_generated_tile_in_server_html():
    from app.server_html import game_card
    html = game_card("sunshade-dodger", "Sunshade Dodger", "Ada", "sunshade-dodger.start.ng", None)
    assert 'class="generated-tile"' in html
    assert 'class="tile-title">Sunshade Dodger<' in html
    assert 'class="tile-by">by Ada<' in html
