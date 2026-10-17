from datetime import datetime, timedelta, timezone

from app.models import GamePlay
from app.routers import games as games_router
from tests.test_publish import env  # noqa: F401 (fixture)


def publish(client, tokens, repo, who="ada"):
    res = client.post("/games/publish", json={"repo_url": f"https://github.com/{who}/{repo}"}, headers=tokens[who])
    assert res.status_code == 200, res.text


def plays(client):
    return {g["name"]: g["plays"] for g in client.get("/games").json()}


def test_plays_are_counted_once_per_person_for_a_while(env):
    client, _, tokens, _ = env
    publish(client, tokens, "lane-dash")
    publish(client, tokens, "moon-run")
    me, other = {"user-agent": "phone"}, {"user-agent": "laptop"}
    assert client.post("/games/lane-dash/plays", headers=me).status_code == 204
    client.post("/games/lane-dash/plays", headers=me)          # again straight away: same play
    client.post("/games/lane-dash/plays", headers=other)
    client.post("/games/moon-run/plays", headers=me)
    assert plays(client) == {"lane-dash": 2, "moon-run": 1}


def test_only_the_last_week_counts(env, monkeypatch):
    client, _, tokens, _ = env
    publish(client, tokens, "lane-dash")
    client.post("/games/lane-dash/plays", headers={"user-agent": "a"})
    db = next(client.app.dependency_overrides[games_router.get_db]())
    db.add(GamePlay(game_name="lane-dash", visitor="old", at=datetime.now(timezone.utc) - timedelta(days=8)))
    db.commit()
    assert plays(client) == {"lane-dash": 1}


def test_unknown_game(env):
    client, *_ = env
    assert client.post("/games/nope/plays").status_code == 404
