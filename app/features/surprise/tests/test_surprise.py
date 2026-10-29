import random
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.features.surprise import service
from app.features.tags.service import TagError
from app.main import app

LIVE = ["lane-dash", "moon-garden", "whot", "hangout", "ludo"]
RACING = {"lane-dash"}


@pytest.fixture
def fake(monkeypatch):
    """Discovery and tags as their contracts answer: live games newest first, recent opens, a category's games."""
    monkeypatch.setattr(service.discovery, "visible", lambda db: [SimpleNamespace(name=n) for n in LIVE])
    monkeypatch.setattr(service.discovery, "continue_playing", lambda db, user_id: ["whot", "ludo"] if user_id == 1 else [])

    def games_of(db, slug, page=1):
        if slug != "racing-and-driving":
            raise TagError("No such category", 404)
        return SimpleNamespace(slug=slug), len(RACING), [SimpleNamespace(name=n) for n in RACING] if page == 1 else []
    monkeypatch.setattr(service.tags, "games_of", games_of)


def picks(n=60, **kw):
    rng = random.Random(7)
    return {service.pick(None, rng=rng, **kw) for _ in range(n)}


def test_any_live_game_but_never_the_hangout(fake):
    assert picks() == {"lane-dash", "moon-garden", "whot", "ludo"}


def test_not_the_game_you_are_on(fake):
    assert "whot" not in picks(exclude="whot")


def test_games_you_opened_lately_come_last(fake):
    assert picks(user_id=1) == {"lane-dash", "moon-garden"}
    assert picks(user_id=1, tag="racing-and-driving") == {"lane-dash"}


def test_everything_opened_lately_still_gives_a_game(fake, monkeypatch):
    monkeypatch.setattr(service.discovery, "continue_playing", lambda db, user_id: LIVE)
    assert picks(user_id=1) == {"lane-dash", "moon-garden", "whot", "ludo"}


def test_a_category(fake):
    assert picks(tag="racing-and-driving") == {"lane-dash"}
    with pytest.raises(TagError):
        service.pick(None, tag="no-such-thing")


def test_the_route(fake, monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/discover/surprise").status_code == 404  # ships dark
    monkeypatch.setattr(settings, "features", "surprise")
    assert client.get("/discover/surprise").json()["game"] in {"lane-dash", "moon-garden", "whot", "ludo"}
    assert client.get("/discover/surprise?tag=racing-and-driving").json() == {"game": "lane-dash"}
    assert client.get("/discover/surprise?tag=no-such-thing").status_code == 404
    monkeypatch.setattr(service.discovery, "visible", lambda db: [SimpleNamespace(name="hangout")])
    assert client.get("/discover/surprise").json()["detail"] == "No games to pick from"
