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


def test_the_games_own_cover_png_gets_a_card_sized_copy(env):
    """Ziki #74: cards use a 640 x 400 WebP copy of the repo's cover.png; the original stays as it is."""
    client, storage, tokens, archives = env
    big = picture(size=(1600, 1000))
    archives["zip"] = make_zip({**GAME, "cover.png": big})
    publish(client, tokens)
    assert cover_of(client).startswith(f"https://lane-dash.start.ng/{covers.CARD_FILE}?v=")
    card = Image.open(storage.root / "lane-dash" / covers.CARD_FILE)
    assert (card.format, card.size) == ("WEBP", covers.SIZE)
    assert (storage.root / "lane-dash" / "cover.png").read_bytes() == big


def test_a_cover_that_cant_be_read_is_used_as_it_is(env):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": b"not really a png"})
    publish(client, tokens)
    assert cover_of(client).startswith("https://lane-dash.start.ng/cover.png?v=")
    assert not (storage.root / "lane-dash" / covers.CARD_FILE).exists()


def test_a_repo_file_cant_replace_the_card_copy(env):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": picture(), covers.CARD_FILE: b"from the repo"})
    publish(client, tokens)
    assert Image.open(storage.root / "lane-dash" / covers.CARD_FILE).format == "WEBP"


def test_the_share_picture_is_small_enough_for_whatsapp(env):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": picture(size=(1600, 1000))})
    publish(client, tokens)
    share = next(g["share_url"] for g in client.get("/games").json() if g["name"] == "lane-dash")
    assert share.startswith("https://api.start.ng/games/lane-dash/share.jpg?v=")
    res = client.get("/games/lane-dash/share.jpg")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert "immutable" in res.headers["cache-control"]
    img = Image.open(io.BytesIO(res.content))
    assert (img.format, img.size) == ("JPEG", covers.SHARE_SIZE) and len(res.content) < 300_000
    page = client.get("/pages/games/lane-dash").text
    assert f'<meta property="og:image" content="{share}" />' in page.replace("&amp;", "&")
    assert '<meta property="og:image:width" content="1200" />' in page and '<meta property="og:image:height" content="630" />' in page


def test_no_cover_no_share_picture(env):
    client, _, tokens, _ = env
    publish(client, tokens)
    assert next(g["share_url"] for g in client.get("/games").json() if g["name"] == "lane-dash") is None
    assert client.get("/games/lane-dash/share.jpg").status_code == 404
    assert client.get("/games/nope/share.jpg").status_code == 404
    assert "og:image:width" not in client.get("/pages/games/lane-dash").text  # start.ng's own picture has its own size


def test_backfill_gives_older_games_their_card_copy(env, monkeypatch):
    from app.config import settings
    from app.db import get_db
    from app.main import app
    from app.models import Game, User
    client, storage, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "cover.png": picture()})
    publish(client, tokens)
    db = next(app.dependency_overrides[get_db]())
    game = db.get(Game, "lane-dash")
    game.cover = "cover.png"  # as published before Ziki #74
    db.get(User, game.owner_id).google_sub = "g-ada"  # admins sign in with Google
    db.commit()
    (storage.root / "lane-dash" / covers.CARD_FILE).unlink()
    assert client.post("/admin/covers/backfill", headers=tokens["ada"]).status_code in (403, 404)  # admins only
    monkeypatch.setattr(settings, "super_admin_emails", "ada@example.com")
    assert client.post("/admin/covers/backfill", headers=tokens["ada"]).json() == {"made": 1, "tried": 1, "left": 0}
    assert covers.CARD_FILE in cover_of(client)
    assert client.post("/admin/covers/backfill", headers=tokens["ada"]).json() == {"made": 0, "tried": 0, "left": 0}


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
    assert f"/{covers.CARD_FILE}?v=" in res.json()["cover_url"]  # the game's own cover again, through its card copy
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
