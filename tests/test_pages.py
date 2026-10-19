import json

import pytest

from app.leaderboard import parse_description
from app.models import Game
from app.routers import pages
from tests.test_publish import env  # noqa: F401 (fixture)

SHELL = (
    '<!doctype html><html><head><!--seo-->\n    <title>start.ng</title>\n    <!--/seo--></head>'
    '<body><div id="root"></div><script src="/assets/app.js"></script></body></html>'
)


@pytest.fixture(autouse=True)
def shell(monkeypatch):
    monkeypatch.setattr(pages, "_shell", None)
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)


def test_description_from_startng_json():
    assert parse_description(b'{"description": "  Dodge   the\\nlava.  "}') == "Dodge the lava."
    assert parse_description(b'{"description": "x"}') == "x"
    assert parse_description(b'{"description": 5}') is None
    assert parse_description(b'{"leaderboard": {}}') is None
    assert parse_description(b"not json") is None
    assert len(parse_description(json.dumps({"description": "a" * 1000}).encode())) == 300


def add_game(client_env, name="lane-dash", description=None):
    # The env fixture makes users but no games; add one straight to the database.
    from app.db import get_db
    from app.main import app

    db = next(app.dependency_overrides[get_db]())
    db.add(Game(name=name, owner_id=1, repo_url=f"https://github.com/ada/{name}", description=description))
    db.commit()


def test_game_page_is_rendered_for_crawlers(env):
    client, *_ = env
    add_game(env, description="Dodge the lava <b>fast</b>.")
    res = client.get("/pages/games/lane-dash")
    assert res.status_code == 200
    text = res.text
    assert "<title>Lane Dash · Play free on start.ng</title>" in text
    assert 'rel="canonical" href="https://start.ng/games/lane-dash"' in text
    assert 'property="og:title" content="Lane Dash · Play free on start.ng"' in text
    assert "Dodge the lava &lt;b&gt;fast&lt;/b&gt;." in text  # escaped, never raw HTML
    assert "<b>fast</b>" not in text
    assert '"@type": "VideoGame"' in text
    assert '<div id="root"><main><h1>Lane Dash</h1>' in text
    assert "/assets/app.js" in text  # the app still loads
    assert "index, follow" in text


def test_game_page_without_description_writes_one(env):
    client, *_ = env
    add_game(env)
    text = client.get("/pages/games/lane-dash").text
    assert "Play Lane Dash free in your browser on start.ng, a game by Ada." in text


def test_unknown_game_is_a_404_that_stays_out_of_search(env):
    client, *_ = env
    res = client.get("/pages/games/nope")
    assert res.status_code == 404
    assert "noindex" in res.text
    assert "Game not found" in res.text


def test_bad_names_are_refused(env):
    client, *_ = env
    assert client.get("/pages/games/Bad_Name").status_code == 422


def test_page_unavailable_when_the_website_cant_be_read(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(pages, "_get_shell", lambda: None)
    assert client.get("/pages/games/lane-dash").status_code == 503


def test_sitemap_lists_pages_and_games(env):
    client, *_ = env
    add_game(env, "lane-dash")
    add_game(env, "moon-garden")
    res = client.get("/pages/sitemap.xml")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/xml")
    for loc in ["https://start.ng/", "https://start.ng/games", "https://start.ng/games/lane-dash", "https://start.ng/games/moon-garden"]:
        assert f"<loc>{loc}</loc>" in res.text


def test_publishing_saves_the_description_and_it_reaches_the_api(env):
    from tests.test_publish import GAME, make_zip

    client, _, tokens, archives = env
    manifest = json.dumps({"description": "Dodge the lava and climb as high as you can."})
    archives["zip"] = make_zip({**GAME, "startng.json": manifest})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert res.status_code == 200, res.text
    listed = client.get("/games").json()
    assert listed[0]["description"] == "Dodge the lava and climb as high as you can."
    assert "Dodge the lava and climb as high as you can." in client.get("/pages/games/lane-dash").text

    # Publishing again without a description clears it: the repo is the source of truth.
    archives["zip"] = make_zip(GAME)
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert client.get("/games").json()[0]["description"] is None
