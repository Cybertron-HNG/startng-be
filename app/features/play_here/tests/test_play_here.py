import pytest

from app.config import settings
from app.routers import pages
from tests.test_pages import SHELL, add_game
from tests.test_publish import env  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def shell(monkeypatch):
    monkeypatch.setattr(pages, "_shell", None)
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)


def test_the_game_page_links_to_play_here(env, monkeypatch):
    monkeypatch.setattr(settings, "features", "play_here")
    client, *_ = env
    add_game(env)
    html = client.get("/pages/games/lane-dash").text
    assert '<p class="play-here-link"><a class="btn ghost" href="/games/lane-dash?play=here">Play here</a></p>' in html
    assert html.count("Play here") == 1


def test_nothing_while_the_flag_is_off(env, monkeypatch):
    monkeypatch.setattr(settings, "features", "")
    client, *_ = env
    add_game(env)
    assert "Play here" not in client.get("/pages/games/lane-dash").text
