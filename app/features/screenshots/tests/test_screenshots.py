"""Screenshots (Ziki #19): the startng.json section, the API, and the game's server-written page."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import manifest
from app.config import settings
from app.db import Base, get_db
from app.features.screenshots import service
from app.main import app
from app.models import Game, User
from app.routers import pages

SHELL = '<html><head><!--seo--><title>x</title><!--/seo--></head><body><div id="root"></div></body></html>'


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(settings, "features", "screenshots")
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        ada = User(email="ada@example.com", name="Ada")
        db.add(ada)
        db.flush()
        db.add_all([Game(name="lagos-run", owner_id=ada.id, repo_url="a"), Game(name="quiet", owner_id=ada.id, repo_url="b")])
        db.commit()
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def publish(Session, name, raw: dict):
    """What publishing does with a game's startng.json: parse every section, then let each save its value."""
    with Session() as db:
        manifest.apply_sections(db, db.get(Game, name), manifest.parse_sections(json.dumps(raw).encode()))
        db.commit()


def test_a_game_lists_its_screenshots_in_order(env):
    client, Session = env
    publish(Session, "lagos-run", {"screenshots": ["shots/level-1.png", "/shots/boss.webp", "shots/level-1.png"]})
    shots = client.get("/games/lagos-run/screenshots").json()["screenshots"]
    assert shots == [  # leading slashes dropped, repeats listed once
        {"url": "https://lagos-run.start.ng/shots/level-1.png", "alt": "Lagos Run screenshot 1"},
        {"url": "https://lagos-run.start.ng/shots/boss.webp", "alt": "Lagos Run screenshot 2"},
    ]
    assert client.get("/games/quiet/screenshots").json() == {"screenshots": []}  # lists none
    publish(Session, "lagos-run", {"screenshots": []})  # an empty list removes them
    assert client.get("/games/lagos-run/screenshots").json() == {"screenshots": []}


@pytest.mark.parametrize("value,words", [
    ("shots/1.png", "must be a list"),
    (["a.png", "b.png", "c.png", "d.png", "e.png"], "at most 4"),
    ([3], "path of an image"),
    (["../other-game/cover.png"], "usable path"),
    ([".secret/shot.png"], "usable path"),
    (["shots\\1.png"], "usable path"),
    (["shots/clip.mp4"], ".png, .jpg, .jpeg or .webp"),
])
def test_a_broken_list_refuses_the_publish_with_a_clear_message(value, words):
    with pytest.raises(manifest.SchemaError, match=words.replace(".", r"\.")):
        manifest.parse_sections(json.dumps({"screenshots": value}).encode())


def test_the_server_page_shows_the_same_pictures_and_lists_them_for_search(env):
    client, Session = env
    publish(Session, "lagos-run", {"screenshots": ["shots/1.png"]})
    page = client.get("/pages/games/lagos-run").text
    assert '<section class="game-shots" aria-label="Screenshots"><p class="eyebrow">Screenshots</p>' in page
    assert ('<a href="https://lagos-run.start.ng/shots/1.png"><img src="https://lagos-run.start.ng/shots/1.png" '
            'alt="Lagos Run screenshot 1" loading="lazy" /></a>') in page
    assert '"screenshot": ["https://lagos-run.start.ng/shots/1.png"]' in page  # in the VideoGame JSON-LD
    quiet = client.get("/pages/games/quiet").text
    assert "game-shots" not in quiet and '"screenshot"' not in quiet  # no pictures, no empty section


def test_off_without_its_flag_and_404_for_unknown_or_hidden_games(env, monkeypatch):
    client, Session = env
    publish(Session, "lagos-run", {"screenshots": ["shots/1.png"]})
    assert client.get("/games/nope/screenshots").status_code == 404
    with Session() as db:
        db.get(Game, "quiet").hidden = "flagged"
        db.commit()
    assert client.get("/games/quiet/screenshots").status_code == 404
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/games/lagos-run/screenshots").status_code == 404  # ships dark
    page = client.get("/pages/games/lagos-run").text
    assert "game-shots" not in page and '"screenshot"' not in page
    with Session() as db:
        assert service.screenshots_of(db, "lagos-run") == []
