"""The start.ng Kit and the game standards (app/checks.py): a game that loads https://start.ng/kit.js gets the back
button and the start.ng badge from it, and Share on X from its end-of-round card."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.checks import standards, uses_kit
from app.db import Base, get_db
from app.main import app
from app.models import Game, User
from app.security import create_token
from tests.test_publish import GAME, make_zip

KIT = '<script src="https://start.ng/kit.js" defer></script>'
END = "src/game.js"
GAME_OVER = "function end() { startng.gameOver({ score }); }"


def files(index, script=GAME_OVER):
    return [(path, data.encode() if isinstance(data, str) else data) for path, data in {**GAME, "index.html": index, END: script}.items()]


def missing(index, script=GAME_OVER):
    return [p.code for p in standards(files(index, script))]


def test_the_kit_and_game_over_meet_all_three():
    assert missing(f"<!doctype html>{KIT}") == []


@pytest.mark.parametrize("tag", [
    '<script src="https://start.ng/kit.js" defer></script>',
    "<script defer src='https://start.ng/kit.js'></script>",
    '<SCRIPT SRC="https://start.ng/kit.js?v=2"></SCRIPT>',
    '<script src="https://start.ng/kit/v1.js"></script>',
    '<script src=https://start.ng/kit.js></script>',
])
def test_ways_of_loading_the_kit(tag):
    assert uses_kit(files(tag))
    assert missing(tag) == []


@pytest.mark.parametrize("tag", [
    '<script src="https://evil.example/kit.js"></script>',
    '<script src="https://start.ng.evil.example/kit.js"></script>',
    '<script src="https://start.ng/kit.json"></script>',
    '<!-- <a href="https://start.ng/kit.js"> --> <link href="https://start.ng/kit.js">',
])
def test_other_scripts_are_not_the_kit(tag):
    assert not uses_kit(files(tag))


def test_without_game_over_there_is_no_share_button():
    assert missing(KIT, script="console.log('no end card')") == ["missing_share"]


def test_share_without_the_card_counts():
    assert missing(KIT, script="button.onclick = () => startng.share({ score });") == []


def test_turning_the_back_button_and_badge_off_needs_your_own():
    tag = '<script src="https://start.ng/kit.js" data-back="off" data-badge="off" defer></script>'
    assert missing(tag) == ["missing_back_link", "missing_branding"]
    own = tag + '<a href="https://start.ng">Back to start.ng</a>'
    assert missing(own) == []


def test_a_kit_game_reaches_the_kit_code_only_through_the_script_tag():
    # Calling startng.gameOver without loading the Kit doesn't meet anything.
    assert missing("<!doctype html>") == ["missing_back_link", "missing_branding", "missing_share"]


def test_publishing_records_uses_kit(tmp_path, monkeypatch):
    from app.game_store import LocalGameStore, get_game_store
    from app.routers import games as games_router

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    app.dependency_overrides[get_db] = lambda: (yield Session())
    app.dependency_overrides[get_game_store] = lambda: LocalGameStore(tmp_path)
    archives = {"zip": make_zip(GAME)}
    monkeypatch.setattr(games_router, "download_repo", lambda repo: archives["zip"])
    with Session() as db:
        db.add(User(email="ada@example.com", name="Ada"))
        db.commit()
        token = {"Authorization": f"Bearer {create_token(1)}"}
    client = TestClient(app)
    try:
        assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/plain", "name": "plain"}, headers=token).status_code == 200
        archives["zip"] = make_zip({**GAME, "index.html": f"<!doctype html>{KIT}", END: GAME_OVER})
        assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/kit", "name": "kit-game"}, headers=token).status_code == 200
        with Session() as db:
            assert db.get(Game, "plain").uses_kit is False
            kit_game = db.get(Game, "kit-game")
            assert (kit_game.uses_kit, kit_game.meets_standards) == (True, True)
    finally:
        app.dependency_overrides.clear()
