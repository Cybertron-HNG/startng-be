from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import manifest
from app.config import settings
from app.db import Base, get_db
from app.features.remix.models import GameRemix
from app.main import app
from app.models import Game, User
from app.routers import pages

SHELL = '<!doctype html><html><head><!--seo-->\n    <title>start.ng</title>\n    <!--/seo--></head><body><div id="root"></div></body></html>'


def ago(days=0):
    return datetime.now(timezone.utc) - timedelta(days=days)


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

    monkeypatch.setattr(settings, "features", "remix")
    monkeypatch.setattr(pages, "_shell", None)
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        daniel = User(email="daniel@example.com", name="Daniel", google_sub="g-daniel")
        ada = User(email="ada@example.com", name="Ada", google_sub="g-ada")
        db.add_all([daniel, ada])
        db.flush()
        db.add_all([
            Game(name="coin-rush", owner_id=daniel.id, repo_url="https://github.com/daniel/coin-rush", created_at=ago(30)),
            Game(name="coin-rush-night", owner_id=ada.id, repo_url="https://github.com/ada/coin-rush-night", created_at=ago(2)),
            Game(name="coin-rush-ice", owner_id=ada.id, repo_url="https://github.com/ada/coin-rush-ice", created_at=ago(1)),
        ])
        db.commit()
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def publish(Session, game: str, raw: bytes):
    """What publishing does with a game's startng.json."""
    with Session() as db:
        manifest.apply_sections(db, db.get(Game, game), manifest.parse_sections(raw))
        db.commit()


def test_startng_json_sections_are_checked():
    assert manifest.parse_sections(b'{"remix": true, "remix_of": "coin-rush"}') == {"remix": True, "remix_of": "coin-rush"}
    assert manifest.parse_sections(b'{"remix_of": null}') == {"remix_of": None}
    for bad in (b'{"remix": "yes"}', b'{"remix_of": "Coin Rush!"}', b'{"remix_of": 5}'):
        with pytest.raises(manifest.SchemaError):
            manifest.parse_sections(bad)


def test_remixing_is_off_until_the_creator_turns_it_on(env):
    client, Session = env
    body = client.get("/games/coin-rush/remix").json()
    assert body == {"allowed": False, "fork_url": None, "remix_of": None, "remixes": [], "count": 0}
    publish(Session, "coin-rush", b'{"remix": true}')
    body = client.get("/games/coin-rush/remix").json()
    assert body["allowed"] is True and body["fork_url"] == "https://github.com/daniel/coin-rush/fork"
    publish(Session, "coin-rush", b'{"remix": false}')
    assert client.get("/games/coin-rush/remix").json()["fork_url"] is None


def test_credit_both_ways(env):
    client, Session = env
    publish(Session, "coin-rush-night", b'{"remix_of": "coin-rush"}')
    publish(Session, "coin-rush-ice", b'{"remix_of": "coin-rush"}')
    remix = client.get("/games/coin-rush-night/remix").json()
    assert remix["remix_of"] == {"name": "coin-rush", "title": "Coin Rush", "by": "Daniel"}
    original = client.get("/games/coin-rush/remix").json()
    assert original["count"] == 2 and [g["name"] for g in original["remixes"]] == ["coin-rush-ice", "coin-rush-night"]


def test_hidden_games_and_self_remixes_are_left_out(env):
    client, Session = env
    publish(Session, "coin-rush-night", b'{"remix_of": "coin-rush"}')
    publish(Session, "coin-rush", b'{"remix_of": "coin-rush"}')  # a game can't be its own remix
    with Session() as db:
        assert db.get(GameRemix, "coin-rush").remix_of is None
        db.get(Game, "coin-rush-night").hidden = "reported"
        db.commit()
    assert client.get("/games/coin-rush/remix").json()["count"] == 0
    assert client.get("/games/coin-rush-night/remix").status_code == 404
    with Session() as db:  # and the original being hidden hides the credit line, not the remix
        db.get(Game, "coin-rush-night").hidden = None
        db.get(Game, "coin-rush").hidden = "flagged"
        db.commit()
    assert client.get("/games/coin-rush-night/remix").json()["remix_of"] is None


def test_unknown_game_is_404(env):
    client, _ = env
    assert client.get("/games/nope/remix").status_code == 404


def test_the_server_page_has_the_same_lines(env):
    client, Session = env
    publish(Session, "coin-rush", b'{"remix": true}')
    publish(Session, "coin-rush-night", b'{"remix_of": "coin-rush"}')
    original = client.get("/pages/games/coin-rush").text
    assert '<section class="game-remix" aria-label="Remix"><span>Remix</span>' in original
    assert 'Remixed once: <a href="/games/coin-rush-night">Coin Rush Night</a>' in original
    assert 'href="https://github.com/daniel/coin-rush/fork"' in original and ">Remix it</a>" in original
    remix = client.get("/pages/games/coin-rush-night").text
    assert 'Remixed from <a href="/games/coin-rush">Coin Rush</a> by Daniel' in remix and "Remix it" not in remix


def test_nothing_on_the_page_while_the_flag_is_off(env, monkeypatch):
    client, Session = env
    publish(Session, "coin-rush", b'{"remix": true}')
    monkeypatch.setattr(settings, "features", "")
    assert "game-remix" not in client.get("/pages/games/coin-rush").text
    assert client.get("/games/coin-rush/remix").status_code == 404
