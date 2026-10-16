"""Keeping games in step with their GitHub repos (app/auto_update.py)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app import auto_update
from app.auto_update import Commit, check_due_games, check_every, is_due, latest_commit
from app.db import get_db
from app.main import app
from app.models import Game
from tests.test_publish import env, make_zip  # noqa: F401  (env is a fixture)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def setup(env, monkeypatch):  # noqa: F811
    client, storage, tokens, archives = env
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/lane-dash"}, headers=tokens["ada"])
    session = next(app.dependency_overrides[get_db]())
    make_session = sessionmaker(bind=session.get_bind(), autoflush=False)
    answers = {}  # what the fake GitHub says: repo -> Commit
    asked = []

    def fake_latest(repo_url, branch, etag=None):
        asked.append((repo_url, etag))
        return answers.get(repo_url, Commit("same", etag=etag))

    monkeypatch.setattr(auto_update, "latest_commit", fake_latest)

    def run(now=NOW):
        return check_due_games(make_session, now=now, store=storage)

    def game():
        with make_session() as db:
            return db.get(Game, "lane-dash")

    def set_game(**fields):
        with make_session() as db:
            g = db.get(Game, "lane-dash")
            for k, v in fields.items():
                setattr(g, k, v)
            db.commit()

    return run, game, set_game, answers, asked, storage, archives


def test_how_often_games_are_checked():
    g = Game(name="x", owner_id=1, repo_url="u")
    g.updated_at = NOW - timedelta(hours=2)
    assert check_every(g, NOW) == timedelta(minutes=1)
    g.updated_at = NOW - timedelta(days=3)
    assert check_every(g, NOW) == timedelta(minutes=5)
    g.updated_at = NOW - timedelta(days=90)
    assert check_every(g, NOW) == timedelta(hours=1)
    g.checked_at = NOW - timedelta(minutes=30)
    assert not is_due(g, NOW)
    g.checked_at = NOW - timedelta(minutes=61)
    assert is_due(g, NOW)


def test_first_look_records_whats_live(setup):
    run, game, set_game, answers, _, _, _ = setup
    answers["https://github.com/ada/lane-dash"] = Commit("fresh", sha="a" * 40, etag='"e1"')
    summary = run()
    assert summary.checked == 1 and summary.updated == []
    assert game().last_commit == "a" * 40 and game().commit_etag == '"e1"'


def test_unchanged_repos_are_left_alone_and_send_their_fingerprint(setup):
    run, game, set_game, answers, asked, storage, _ = setup
    set_game(last_commit="a" * 40, commit_etag='"e1"')
    summary = run()
    assert summary.updated == []
    assert asked == [("https://github.com/ada/lane-dash", '"e1"')]
    assert game().checked_at is not None


def test_a_check_isnt_an_update(setup):
    run, game, set_game, answers, _, _, _ = setup
    old = datetime(2026, 9, 1, tzinfo=timezone.utc)
    set_game(updated_at=old)
    run()
    answers["https://github.com/ada/lane-dash"] = Commit("fresh", sha="a" * 40, etag='"e1"')
    run(NOW + timedelta(hours=2))
    assert game().updated_at.replace(tzinfo=timezone.utc) == old  # list order and check rate unchanged


def test_a_new_commit_is_published(setup):
    run, game, set_game, answers, _, storage, archives = setup
    set_game(last_commit="a" * 40, commit_etag='"e1"', updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    owner = game().owner_id
    archives["zip"] = make_zip({"index.html": "<h1>v2</h1>"})
    answers["https://github.com/ada/lane-dash"] = Commit("fresh", sha="b" * 40, etag='"e2"')
    summary = run()
    assert summary.updated == ["lane-dash"]
    assert (storage.root / "lane-dash" / "index.html").read_text() == "<h1>v2</h1>"
    assert game().last_commit == "b" * 40 and game().commit_etag == '"e2"'
    assert game().owner_id == owner  # still theirs
    assert game().updated_at.replace(tzinfo=timezone.utc) > datetime(2026, 9, 1, tzinfo=timezone.utc)  # republished: moves up


def test_games_arent_checked_before_theyre_due(setup):
    run, game, set_game, answers, asked, _, _ = setup
    run()
    run(NOW + timedelta(seconds=30))  # updated just now: checked every minute
    assert len(asked) == 1
    run(NOW + timedelta(minutes=2))
    assert len(asked) == 2


def test_a_broken_commit_doesnt_break_the_live_game(setup):
    run, game, set_game, answers, _, storage, archives = setup
    set_game(last_commit="a" * 40)
    before = (storage.root / "lane-dash" / "index.html").read_text()
    archives["zip"] = make_zip({"README.md": "oops, no game"})
    answers["https://github.com/ada/lane-dash"] = Commit("fresh", sha="c" * 40, etag='"e3"')
    summary = run()
    assert summary.updated == []
    assert (storage.root / "lane-dash" / "index.html").read_text() == before
    assert game().last_commit == "c" * 40  # not retried until the next commit


def test_github_limit_pauses_checking(setup):
    run, game, set_game, answers, _, _, _ = setup
    later = NOW + timedelta(minutes=20)
    answers["https://github.com/ada/lane-dash"] = Commit("limited", retry_at=later)
    summary = run()
    assert summary.paused_until == later and summary.checked == 0
    assert game().checked_at is None  # still due once the limit resets


def test_reading_githubs_answers(monkeypatch):
    class Res:
        def __init__(self, status, text="", headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    replies = {}
    sent = {}

    def fake_get(url, headers, timeout):
        sent["url"], sent["headers"] = url, headers
        return replies["next"]

    monkeypatch.setattr(auto_update.requests, "get", fake_get)

    replies["next"] = Res(200, "d" * 40 + "\n", {"ETag": '"e9"'})
    found = latest_commit("https://github.com/ada/lane-dash", None)
    assert (found.status, found.sha, found.etag) == ("fresh", "d" * 40, '"e9"')
    assert sent["url"] == "https://api.github.com/repos/ada/lane-dash/commits/HEAD"
    assert sent["headers"]["Accept"] == "application/vnd.github.sha"

    replies["next"] = Res(304)
    assert latest_commit("https://github.com/ada/lane-dash", "main", '"e9"').status == "same"
    assert sent["headers"]["If-None-Match"] == '"e9"' and sent["url"].endswith("/commits/main")

    replies["next"] = Res(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"})
    limited = latest_commit("https://github.com/ada/lane-dash", None)
    assert limited.status == "limited" and limited.retry_at == datetime.fromtimestamp(1800000000, timezone.utc)

    replies["next"] = Res(404)
    assert latest_commit("https://github.com/ada/lane-dash", None).status == "missing"
