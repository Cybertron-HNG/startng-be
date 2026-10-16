"""Publishing without signing in: a new repo waits until someone approves it."""
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from app.db import get_db
from app.main import app
from app.models import Game, PendingGame
from app.routers import games as games_router
from tests.test_publish import env, make_zip  # noqa: F401  (env is a fixture)

REPO = "https://github.com/builder/Tower-Stack"


def anon_publish(client, repo=REPO, name=None):
    return client.post("/games/publish", json={"repo_url": repo, **({"name": name} if name else {})})


def code_from(res):
    return res.json()["approve_url"].rsplit("/approve/", 1)[1]


def db_session():
    return next(app.dependency_overrides[get_db]())


def test_new_repo_waits_for_approval(env, monkeypatch):
    client, storage, tokens, _ = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    res = anon_publish(client)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "pending" and body["name"] == "tower-stack"
    assert body["approve_url"].startswith("https://start.ng/approve/")
    assert body["files"] == 3 and "approve" in body["message"]
    # Nothing is live yet and the name isn't anyone's.
    assert storage.paths("tower-stack") == []
    assert client.get("/games").json() == []

    info = client.get(f"/games/publish/{code_from(res)}").json()
    assert info["name"] == "tower-stack" and info["available"] is True


def test_approving_puts_it_live_on_your_account(env, monkeypatch):
    client, storage, tokens, _ = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    code = code_from(anon_publish(client))
    assert client.post(f"/games/publish/{code}/approve").status_code == 401  # must sign in

    res = client.post(f"/games/publish/{code}/approve", headers=tokens["ada"])
    assert res.status_code == 200, res.text
    assert res.json()["created"] is True
    assert storage.paths("tower-stack") == ["favicon.svg", "index.html", "src/game.js"]
    assert [(g["name"], g["by"]) for g in client.get("/games").json()] == [("tower-stack", "Ada")]
    # The link is spent.
    assert client.get(f"/games/publish/{code}").status_code == 404
    assert client.post(f"/games/publish/{code}/approve", headers=tokens["ada"]).status_code == 404


def test_an_approved_repo_updates_straight_away(env, monkeypatch):
    client, storage, tokens, archives = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    client.post(f"/games/publish/{code_from(anon_publish(client))}/approve", headers=tokens["ada"])

    archives["zip"] = make_zip({"index.html": "<h1>v2</h1>"})
    res = anon_publish(client, repo=REPO.lower())  # GitHub links ignore case
    assert res.status_code == 200, res.text
    assert res.json()["created"] is False
    assert (storage.root / "tower-stack" / "index.html").read_text() == "<h1>v2</h1>"
    # Still Ada's.
    assert client.get("/games").json()[0]["by"] == "Ada"
    # It can't be pointed at another name.
    assert anon_publish(client, name="other").status_code == 409


def test_names_taken_by_other_repos_are_refused_early(env, monkeypatch):
    client, _, tokens, _ = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/x", "name": "tower-stack"}, headers=tokens["ada"])
    res = anon_publish(client)
    assert res.status_code == 409 and "taken" in res.json()["detail"]


def test_the_first_approval_wins(env, monkeypatch):
    client, _, tokens, _ = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    first = code_from(anon_publish(client))
    second = code_from(anon_publish(client))  # e.g. published twice before approving
    assert client.post(f"/games/publish/{first}/approve", headers=tokens["ada"]).status_code == 200
    # Approving put the repo on Ada's account, so every other link for it is spent.
    assert client.post(f"/games/publish/{second}/approve", headers=tokens["tunde"]).status_code == 404


def test_expired_links_dont_work(env, monkeypatch):
    client, _, tokens, _ = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    code = code_from(anon_publish(client))
    db = db_session()
    db.get(PendingGame, code).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert client.get(f"/games/publish/{code}").status_code == 404
    assert client.post(f"/games/publish/{code}/approve", headers=tokens["ada"]).status_code == 404
    assert db.get(Game, "tower-stack") is None


def test_bad_games_are_reported_before_approval(env, monkeypatch):
    client, _, _, archives = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    archives["zip"] = make_zip({"README.md": "no game here"})
    res = anon_publish(client)
    assert res.status_code == 422 and "index.html" in res.json()["detail"]


def test_new_games_without_signing_in_are_rate_limited(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(games_router, "_recent", defaultdict(deque))
    monkeypatch.setattr(games_router, "ANONYMOUS_PER_HOUR", 2)
    assert anon_publish(client, repo="https://github.com/a/one").status_code == 202
    assert anon_publish(client, repo="https://github.com/a/two").status_code == 202
    assert anon_publish(client, repo="https://github.com/a/three").status_code == 429
