"""What start.ng checks inside a game before it goes live (app/checks.py), and how problems
come back: POST /games/check, POST /games/publish, and failed updates in Your games."""
from collections import defaultdict, deque
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app import auto_update
from app.auto_update import Commit, check_due_games
from app.checks import inspect
from app.db import get_db
from app.main import app
from app.publish import GameRejected, read_game
from app.routers import games as games_router
from tests.test_publish import GAME, env, make_zip  # noqa: F401  (env is a fixture)

# A game that does everything right: no errors, no warnings.
GOOD = {
    **GAME,
    "src/game.js": "const r = await askStartNg({ type: 'startng:whoami' }, 'startng:user');",
    "cover.png": b"\x89PNG\r\n\x1a\n...",
}
# Built at runtime so this file itself never matches the team's malware gate.
HIDDEN_RUN = "ev" + "al(at" + "ob('aGk='))"
MINER = "new Coin" + "Hive.Anonymous('key').start()"


def codes(problems):
    return [p.code for p in problems]


def check(files):
    return inspect([(path, data.encode() if isinstance(data, str) else data) for path, data in files.items()])


def test_a_good_game_has_nothing_to_report():
    assert check(GOOD) == ([], [])


@pytest.mark.parametrize(
    "path, data, code",
    [
        ("src/game.js", MINER, "crypto_miner"),
        ("src/game.js", "Func" + "tion(String.fromCharCode(97))()", "obfuscated_code"),
        ("src/game.js", "var a=" + "_0x1a2b,".join([""] * 400), "obfuscated_code"),
        ("src/game.js", "'" + "\\x41" * 400 + "'", "obfuscated_code"),
        ("index.html", '<form><input type="password" name="p"></form>', "password_field"),
        ("src/game.js", "input.type = 'password'", "password_field"),
        ("index.html", '<meta http-equiv="refresh" content="0; url=https://evil.example/">', "redirect"),
        ("logo.png", b"MZ\x90\x00 not really a picture", "hidden_program"),
        ("src/game.js", "const p = 'TVqQ" + "AAMAAAAEAAAA//8AALgAAAA'", "hidden_program"),
    ],
)
def test_harmful_files_are_errors(path, data, code):
    errors, _ = check({**GOOD, path: data})
    assert code in codes(errors)
    assert next(p for p in errors if p.code == code).file == path


def test_things_that_look_alike_but_are_fine():
    fine = {
        **GOOD,
        # start.ng's own pages, sign-in links and a meta refresh within start.ng
        "index.html": '<meta http-equiv="refresh" content="5; url=https://start.ng/">'
        '<script src="https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"></script>',
        "src/signin.js": "location.href = 'https://start.ng/?return_to=' + encodeURIComponent(location.href)",
        "src/menu.js": "const label = 'Password'; const hint = 'eval is not used here';",
    }
    errors, warnings = check(fine)
    assert errors == [] and warnings == []


@pytest.mark.parametrize(
    "path, data, code",
    [
        ("index.html", '<script src="https://sketchy.example/lib.js"></script><script>startng:whoami</script>', "external_script"),
        ("src/game.js", "import x from 'https://sketchy.example/x.js'; 'startng:whoami'", "external_script"),
        ("index.html", '<img src="http://example.com/a.png"><script>startng:whoami</script>', "insecure_link"),
        ("src/game.js", "location.href = 'https://elsewhere.example/'; 'startng:whoami'", "leaves_start_ng"),
    ],
)
def test_risky_files_are_warnings(path, data, code):
    errors, warnings = check({**GOOD, path: data})
    assert errors == [] and code in codes(warnings)


def test_missing_cover_and_player_are_warnings():
    errors, warnings = check(GAME)
    assert errors == [] and codes(warnings) == ["no_cover", "no_start_ng_player"]


def test_a_broken_startng_json_is_reported_with_everything_else():
    files = {**GOOD, "startng.json": '{"leaderboard": {"stats": []}}', "src/a.js": MINER}
    with pytest.raises(GameRejected) as caught:
        read_game(make_zip(files))
    assert codes(caught.value.errors) == ["bad_startng_json", "crypto_miner"]
    good = {**GOOD, "startng.json": '{"leaderboard": {"stats": [{"key": "waves", "label": "Waves"}]}}'}
    assert read_game(make_zip(good)).leaderboard[0]["key"] == "waves"


def test_every_problem_is_reported_at_once():
    files = {**GOOD, "src/a.js": MINER, "src/b.js": HIDDEN_RUN, "index2.html": '<input type="password">'}
    with pytest.raises(GameRejected) as caught:
        read_game(make_zip(files))
    assert set(codes(caught.value.errors)) >= {"crypto_miner", "malware", "password_field"}
    assert "more problems" in str(caught.value)


# --- The API ------------------------------------------------------------------------


def test_check_says_a_good_game_would_go_live(env, monkeypatch):
    client, storage, _, archives = env
    monkeypatch.setattr(games_router, "_recent_checks", defaultdict(deque))
    archives["zip"] = make_zip(GAME)
    res = client.post("/games/check", json={"repo_url": "https://github.com/ada/Lane-Dash"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["name"] == "lane-dash" and body["update"] is False
    assert codes_of(body["warnings"]) == ["no_cover", "no_start_ng_player", "thin_page"]
    assert storage.paths("lane-dash") == []  # nothing was published
    assert client.get("/games").json() == []


def codes_of(items):
    return [p["code"] for p in items]


def test_check_lists_every_problem(env, monkeypatch):
    client, _, tokens, archives = env
    monkeypatch.setattr(games_router, "_recent_checks", defaultdict(deque))
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/x", "name": "towerstack"}, headers=tokens["ada"])
    archives["zip"] = make_zip({**GAME, "src/game.js": MINER})
    res = client.post("/games/check", json={"repo_url": "https://github.com/tunde/y", "name": "towerstack"})
    assert res.status_code == 422
    body = res.json()
    assert body["ok"] is False
    assert codes_of(body["errors"]) == ["name_taken", "crypto_miner"]
    assert body["errors"][1]["file"] == "src/game.js"
    assert isinstance(body["detail"], str)


def test_checks_are_rate_limited(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(games_router, "_recent_checks", defaultdict(deque))
    monkeypatch.setattr(games_router, "CHECKS_PER_HOUR", 1)
    assert client.post("/games/check", json={"repo_url": "https://github.com/a/b"}).status_code == 200
    res = client.post("/games/check", json={"repo_url": "https://github.com/a/b"})
    assert res.status_code == 429 and codes_of(res.json()["errors"]) == ["rate_limited"]


def test_publish_refuses_with_codes_and_reports_warnings(env):
    client, _, tokens, archives = env
    archives["zip"] = make_zip({"README.md": "no game"})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/x"}, headers=tokens["ada"])
    assert res.status_code == 422 and codes_of(res.json()["errors"]) == ["no_index"]
    assert "index.html" in res.json()["detail"]

    res = client.post("/games/publish", json={"repo_url": "github.com/ada/x"}, headers=tokens["ada"])
    assert res.status_code == 400 and codes_of(res.json()["errors"]) == ["bad_link"]

    archives["zip"] = make_zip(GAME)
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/x"}, headers=tokens["ada"])
    assert res.status_code == 200 and codes_of(res.json()["warnings"]) == ["no_cover", "no_start_ng_player", "thin_page"]


def test_uploading_a_live_repo_again_updates_its_game(env):
    client, _, tokens, _ = env
    first = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash", "name": "lanedash"}, headers=tokens["ada"])
    assert first.status_code == 200
    again = client.post("/games/publish", json={"repo_url": "https://github.com/ada/lane-dash"}, headers=tokens["ada"])
    assert again.status_code == 200 and again.json()["name"] == "lanedash" and again.json()["created"] is False
    assert [g["name"] for g in client.get("/games").json()] == ["lanedash"]
    # Someone else can't take it, and the owner can't fork it under a second name.
    other = client.post("/games/publish", json={"repo_url": "https://github.com/ada/lane-dash"}, headers=tokens["tunde"])
    assert other.status_code == 409 and codes_of(other.json()["errors"]) == ["repo_linked"]
    renamed = client.post("/games/publish", json={"repo_url": "https://github.com/ada/lane-dash", "name": "dash2"}, headers=tokens["ada"])
    assert renamed.status_code == 409 and codes_of(renamed.json()["errors"]) == ["repo_linked"]


def test_owners_see_why_an_update_didnt_go_live(env, monkeypatch):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip(GOOD)
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/lane-dash"}, headers=tokens["ada"])
    session = next(app.dependency_overrides[get_db]())
    make_session = sessionmaker(bind=session.get_bind(), autoflush=False)
    answers = {"sha": "b" * 40}
    monkeypatch.setattr(auto_update, "latest_commit", lambda repo, branch, etag=None: Commit("fresh", sha=answers["sha"], etag='"e"'))
    from app.models import Game

    with make_session() as db:
        db.get(Game, "lane-dash").last_commit = "a" * 40
        db.commit()

    # A push with a miner in it: the old version stays, and the owner is told why.
    archives["zip"] = make_zip({**GOOD, "src/game.js": MINER})
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    assert check_due_games(make_session, now=now, store=storage).updated == []
    mine = client.get("/games/mine", headers=tokens["ada"]).json()[0]
    problem = mine["update_problem"]
    assert problem["commit"] == "b" * 40 and codes_of(problem["errors"]) == ["crypto_miner"]
    assert client.get("/games").json()[0]["update_problem"] is None  # only the owner sees it
    assert "askStartNg" in (storage.root / "lane-dash" / "src/game.js").read_text()

    # The next good push goes live and clears it.
    answers["sha"] = "c" * 40
    archives["zip"] = make_zip(GOOD)
    later = datetime(2026, 10, 16, 13, tzinfo=timezone.utc)
    assert check_due_games(make_session, now=later, store=storage).updated == ["lane-dash"]
    assert client.get("/games/mine", headers=tokens["ada"]).json()[0]["update_problem"] is None


def test_a_github_hiccup_is_retried_not_skipped(env, monkeypatch):
    client, storage, tokens, archives = env
    archives["zip"] = make_zip(GOOD)
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/lane-dash"}, headers=tokens["ada"])
    session = next(app.dependency_overrides[get_db]())
    make_session = sessionmaker(bind=session.get_bind(), autoflush=False)
    monkeypatch.setattr(auto_update, "latest_commit", lambda repo, branch, etag=None: Commit("fresh", sha="b" * 40, etag='"e"'))
    from app.models import Game
    from app.publish import PublishError

    with make_session() as db:
        db.get(Game, "lane-dash").last_commit = "a" * 40
        db.commit()

    def flaky(repo):
        raise PublishError("Couldn't download the repo from GitHub, try again", "download_failed")

    monkeypatch.setattr(games_router, "download_repo", flaky)
    check_due_games(make_session, now=datetime(2026, 10, 16, 12, tzinfo=timezone.utc), store=storage)
    with make_session() as db:
        game = db.get(Game, "lane-dash")
        assert game.last_commit == "a" * 40 and game.commit_etag is None  # still seen as new next time
        assert game.update_problem is None


def test_a_game_with_nothing_to_say_about_itself_gets_a_warning_and_one_that_does_not(env):
    client, _, tokens, archives = env
    repo = {"repo_url": "https://github.com/ada/Lane-Dash"}
    archives["zip"] = make_zip(GAME)
    res = client.post("/games/publish", json=repo, headers=tokens["ada"])
    assert "thin_page" in codes_of(res.json()["warnings"])
    assert "start.ng/games/<name>" in next(w for w in res.json()["warnings"] if w["code"] == "thin_page")["message"]
    archives["zip"] = make_zip({**GAME, "startng.json": '{"description": "Dodge the lava and climb as high as you can."}'})
    res = client.post("/games/publish", json=repo, headers=tokens["ada"])
    assert "thin_page" not in codes_of(res.json()["warnings"])
    archives["zip"] = make_zip({**GAME, "index.html": '<!doctype html><meta name="description" content="Dodge the lava, climb the tower and beat your friends."><script src="src/game.js"></script>'})
    res = client.post("/games/publish", json=repo, headers=tokens["ada"])
    assert "thin_page" not in codes_of(res.json()["warnings"])  # found in the game's own page
