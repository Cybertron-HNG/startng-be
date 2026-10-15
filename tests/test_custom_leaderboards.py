"""Game-defined leaderboards: a startng.json lists the game's own stats and how they rank."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import leaderboard as boards
from app.db import Base, get_db
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import User
from app.publish import read_game
from app.routers import games as games_router
from app.security import create_token

WAVES = {"leaderboard": {"stats": [
    {"key": "waves", "label": "Waves survived"},
    {"key": "kills", "label": "Enemies killed"},
]}}
RACE = {"leaderboard": {"stats": [
    {"key": "gems", "label": "Gems", "order": "highest"},
    {"key": "time", "label": "Time", "order": "lowest", "format": "time"},
]}}


def make_zip(files: dict[str, str], top="game-main") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, text in files.items():
            z.writestr(f"{top}/{path}", text)
    return buf.getvalue()


def game_files(manifest=None, where="startng.json"):
    files = {"index.html": "<h1>game</h1>"}
    if manifest is not None:
        files[where] = manifest if isinstance(manifest, str) else json.dumps(manifest)
    return files


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    store = LocalGameStore(tmp_path)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_game_store] = lambda: store
    archives = {"zip": make_zip(game_files())}
    monkeypatch.setattr(games_router, "download_repo", lambda repo: archives["zip"])
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in ("ada", "tunde", "chioma")]
        db.add_all(users)
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
    yield TestClient(app), tokens, archives
    app.dependency_overrides.clear()


def publish(client, tokens, archives, files, name="wavegame"):
    archives["zip"] = make_zip(files)
    return client.post("/games/publish", json={"repo_url": f"https://github.com/ada/{name}"}, headers=tokens["ada"])


def card(client, name):
    return next(g for g in client.get("/games").json() if g["name"] == name)


def run(client, tokens, who, stats, name="wavegame"):
    return client.post(f"/games/{name}/scores", json={"stats": stats}, headers=tokens[who])


def test_no_startng_json_means_no_leaderboard_until_scores_arrive(env):
    client, tokens, archives = env
    assert publish(client, tokens, archives, game_files()).status_code == 200
    assert card(client, "wavegame")["leaderboard"] is None
    client.post("/games/wavegame/scores", json={"score": 10}, headers=tokens["ada"])  # an older game, plain score
    assert card(client, "wavegame")["leaderboard"] == boards.DEFAULT


def test_the_game_decides_its_columns_and_ranking(env):
    client, tokens, archives = env
    assert publish(client, tokens, archives, game_files(WAVES)).status_code == 200
    assert [c["label"] for c in card(client, "wavegame")["leaderboard"]] == ["Waves survived", "Enemies killed"]
    assert run(client, tokens, "ada", {"waves": 5, "kills": 10}).json() == {"best": 5, "improved": True, "rank": 1, "stats": {"waves": 5, "kills": 10}}
    assert run(client, tokens, "tunde", {"waves": 5, "kills": 30}).json()["rank"] == 1  # same waves, more kills wins
    assert run(client, tokens, "chioma", {"waves": 8, "kills": 2}).json()["rank"] == 1  # more waves beats kills
    board = client.get("/games/wavegame/leaderboard").json()
    assert [(e["name"], e["stats"]) for e in board] == [
        ("Chioma", {"waves": 8, "kills": 2}), ("Tunde", {"waves": 5, "kills": 30}), ("Ada", {"waves": 5, "kills": 10})]


def test_keeps_the_best_run_not_the_last(env):
    client, tokens, archives = env
    publish(client, tokens, archives, game_files(WAVES))
    run(client, tokens, "ada", {"waves": 6, "kills": 50})
    worse = run(client, tokens, "ada", {"waves": 4, "kills": 99}).json()
    assert worse["improved"] is False and worse["stats"] == {"waves": 6, "kills": 50}
    assert run(client, tokens, "ada", {"waves": 6, "kills": 51}).json()["improved"] is True


def test_lowest_wins_for_times(env):
    client, tokens, archives = env
    publish(client, tokens, archives, game_files(RACE), name="relic")
    run(client, tokens, "ada", {"gems": 15, "time": 300}, name="relic")
    run(client, tokens, "tunde", {"gems": 15, "time": 240}, name="relic")  # same gems, faster
    run(client, tokens, "chioma", {"gems": 9, "time": 60}, name="relic")
    assert [e["name"] for e in client.get("/games/relic/leaderboard").json()] == ["Tunde", "Ada", "Chioma"]
    assert card(client, "relic")["leaderboard"][1] == {"key": "time", "label": "Time", "order": "lowest", "format": "time"}


@pytest.mark.parametrize("stats", [
    {"waves": 5},                                # missing a stat
    {"waves": 5, "kills": 1, "coins": 3},        # a stat the game didn't declare
    {"waves": "five", "kills": 1},               # not a number
    {"waves": 5, "kills": -1},                   # below 0
    {"waves": 5, "kills": 1_000_000_001},        # too big
    {"waves": 2.5, "kills": 1},                  # not a whole number
    {"waves": True, "kills": 1},                 # not a number
    {"waves": 5, "kills": "<script>"},           # no text, ever
])
def test_refuses_anything_but_the_declared_numbers(env, stats):
    client, tokens, archives = env
    publish(client, tokens, archives, game_files(WAVES))
    assert run(client, tokens, "ada", stats).status_code == 422


def test_plain_score_is_refused_when_the_game_has_its_own_stats(env):
    client, tokens, archives = env
    publish(client, tokens, archives, game_files(WAVES))
    res = client.post("/games/wavegame/scores", json={"score": 10}, headers=tokens["ada"])
    assert res.status_code == 422 and "waves, kills" in res.json()["detail"]


@pytest.mark.parametrize("manifest, message", [
    ("{not json", "valid JSON"),
    ({"leaderboard": {"stats": []}}, "1 to 5"),
    ({"leaderboard": {"stats": [{"key": "x"}] * 6}}, "1 to 5"),
    ({"leaderboard": {"stats": [{"key": "Bad Key", "label": "x"}]}}, "lowercase"),
    ({"leaderboard": {"stats": [{"key": "a", "label": ""}]}}, "label"),
    ({"leaderboard": {"stats": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]}}, "twice"),
    ({"leaderboard": {"stats": [{"key": "a", "label": "A", "order": "biggest"}]}}, "order"),
    ({"leaderboard": {"stats": [{"key": "a", "label": "A", "format": "money"}]}}, "format"),
])
def test_bad_startng_json_is_explained(env, manifest, message):
    client, tokens, archives = env
    res = publish(client, tokens, archives, game_files(manifest))
    assert res.status_code == 422 and message in res.json()["detail"] and "startng.json" in res.json()["detail"]


def test_startng_json_in_the_build_folder_or_the_repo_top():
    in_dist = read_game(make_zip({"dist/index.html": "<h1>g</h1>", "dist/startng.json": json.dumps(WAVES), "package.json": "{}"}))
    assert boards.parse_manifest(in_dist.manifest)[0]["key"] == "waves"
    at_top = read_game(make_zip({"dist/index.html": "<h1>g</h1>", "startng.json": json.dumps(RACE), "package.json": "{}"}))
    assert boards.parse_manifest(at_top.manifest)[0]["key"] == "gems"


def test_only_the_owner_removes_scores(env):
    client, tokens, archives = env
    publish(client, tokens, archives, game_files(WAVES))
    run(client, tokens, "tunde", {"waves": 999, "kills": 999})
    tunde_id = client.get("/games/wavegame/leaderboard").json()[0]["player_id"]
    assert client.delete(f"/games/wavegame/scores/{tunde_id}", headers=tokens["tunde"]).status_code == 403
    assert client.delete(f"/games/wavegame/scores/{tunde_id}", headers=tokens["ada"]).status_code == 204
    assert client.get("/games/wavegame/leaderboard").json() == []
