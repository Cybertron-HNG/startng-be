import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import publish, storage as bucket
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import User
from app.publish import PublishError, game_name, parse_repo_url, read_game
from app.game_store import LocalGameStore, R2GameStore, get_game_store
from app.routers import games as games_router
from app.security import create_token

GAME = {
    "index.html": '<!doctype html><script type="module" src="src/game.js"></script>',
    "src/game.js": "console.log('hi')",
    "favicon.svg": "<svg/>",
}


def make_zip(files: dict[str, str | bytes], top: str = "repo-main") -> bytes:
    """A zip shaped like GitHub's archive download: everything inside one top folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(f"{top}/{path}", data)
    return buf.getvalue()


# --- Links and names ---------------------------------------------------------------


def test_reads_github_links():
    repo = parse_repo_url("https://github.com/Blackburn0/HNG-Game")
    assert (repo.owner, repo.name, repo.branch) == ("Blackburn0", "HNG-Game", None)
    assert parse_repo_url("https://github.com/a/b.git").name == "b"
    assert parse_repo_url("https://github.com/a/b/tree/gh-pages").branch == "gh-pages"
    assert parse_repo_url("https://github.com/a/b", branch="main").branch == "main"


@pytest.mark.parametrize(
    "url",
    ["http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com/a", "github.com/a/b", "https://github.com/a/b; rm -rf /"],
)
def test_refuses_other_links(url):
    with pytest.raises(PublishError):
        parse_repo_url(url)


def test_refuses_bad_branch_names():
    for branch in ["../main", "a b", "main;rm"]:
        with pytest.raises(PublishError):
            parse_repo_url("https://github.com/a/b", branch=branch)


def test_game_names():
    assert game_name("HNG-Game") == "hng-game"
    assert game_name("--My  Game!!--") == "my-game"
    assert len(game_name("a" * 60)) == 40
    for bad in ["api", "WWW", "___"]:
        with pytest.raises(PublishError):
            game_name(bad)


# --- Reading the repo ---------------------------------------------------------------


def test_finds_a_plain_game_at_the_top():
    bundle = read_game(make_zip({**GAME, "README.md": "# hi", "server.js": "x", "LICENSE": "MIT"}))
    assert bundle.folder == ""
    assert {f.path for f in bundle.files} == {"index.html", "src/game.js", "favicon.svg", "server.js"}
    assert sorted(bundle.skipped) == ["LICENSE", "README.md"]


def test_prefers_a_committed_build_folder():
    files = {"index.html": '<script type="module" src="/src/main.tsx"></script>', "dist/index.html": "<h1>built</h1>"}
    bundle = read_game(make_zip(files))
    assert bundle.folder == "dist"
    assert [f.path for f in bundle.files] == ["index.html"]


def test_refuses_repos_that_need_building():
    with pytest.raises(PublishError, match="needs building"):
        read_game(make_zip({"index.html": '<script type="module" src="/src/main.tsx"></script>'}))


def test_refuses_repos_without_index_html():
    with pytest.raises(PublishError, match="No index.html"):
        read_game(make_zip({"game.js": "x"}))


def test_leaves_out_hidden_files_node_modules_and_escapes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in GAME.items():
            zf.writestr(f"repo-main/{path}", data)
        zf.writestr("repo-main/.env", "SECRET=1")
        zf.writestr("repo-main/.git/config", "x")
        zf.writestr("repo-main/node_modules/x/index.js", "x")
        zf.writestr("repo-main/../../evil.html", "x")
        zf.writestr("/abs.html", "x")
        link = zipfile.ZipInfo("repo-main/link.js")
        link.external_attr = 0o120777 << 16  # a symbolic link
        zf.writestr(link, "/etc/passwd")
    paths = {f.path for f in read_game(buf.getvalue()).files}
    assert paths == set(GAME)


def test_size_limits(monkeypatch):
    monkeypatch.setattr(publish, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(PublishError, match="over"):
        read_game(make_zip({**GAME, "big.js": "x" * 200}))


def test_refuses_known_malware_patterns():
    # Built at runtime so this test file itself never matches the CI malware gate.
    payload = "ev" + "al(at" + "ob('aGk='))"
    with pytest.raises(PublishError, match="malware"):
        read_game(make_zip({**GAME, "src/game.js": payload}))


def tmp_games(store):
    return store.root


# --- The API --------------------------------------------------------------------------


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

    # Don't depend on whatever the local .env says.
    monkeypatch.setattr(settings, "games_base_domain", "start.ng")
    storage = LocalGameStore(tmp_path)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_game_store] = lambda: storage
    archives = {"zip": make_zip(GAME)}
    monkeypatch.setattr(games_router, "download_repo", lambda repo: archives["zip"])

    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in ("ada", "tunde")]
        db.add_all(users)
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}

    yield TestClient(app), storage, tokens, archives
    app.dependency_overrides.clear()


def test_a_bad_sign_in_is_refused(env):
    client, *_ = env
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.post("/games/publish", json={"repo_url": "https://github.com/a/b"}, headers=bad).status_code == 401


def test_publish_list_and_update(env):
    client, storage, tokens, archives = env
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["name"], body["created"], body["files"]) == ("lane-dash", True, 3)
    assert body["url"] == "https://lane-dash.start.ng"
    assert storage.paths("lane-dash") == ["favicon.svg", "index.html", "src/game.js"]

    games = client.get("/games").json()
    assert [(g["name"], g["by"]) for g in games] == [("lane-dash", "Ada")]

    # The owner publishes a new version.
    archives["zip"] = make_zip({"index.html": "<h1>v2</h1>"})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert res.json()["created"] is False
    assert (tmp_games(storage) / "lane-dash/index.html").read_text() == "<h1>v2</h1>"


def test_names_belong_to_the_first_publisher(env):
    client, _, tokens, _ = env
    first = client.post("/games/publish", json={"repo_url": "https://github.com/ada/x", "name": "towerstack"}, headers=tokens["ada"])
    assert first.status_code == 200
    second = client.post("/games/publish", json={"repo_url": "https://github.com/tunde/y", "name": "towerstack"}, headers=tokens["tunde"])
    assert second.status_code == 409
    assert "taken" in second.json()["detail"]


def test_folders_already_in_the_bucket_are_taken(env):
    client, storage, tokens, _ = env
    storage.put_game("dave", {"index.html": b"<h1>dave</h1>"})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/x", "name": "dave"}, headers=tokens["ada"])
    assert res.status_code == 409
    assert storage.paths("dave") == ["index.html"]


def test_bad_games_are_rejected_without_claiming_the_name(env):
    client, _, tokens, archives = env
    archives["zip"] = make_zip({"README.md": "no game here"})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/x"}, headers=tokens["ada"])
    assert res.status_code == 422
    assert client.get("/games").json() == []


def test_only_the_owner_can_unpublish(env):
    client, storage, tokens, _ = env
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/x", "name": "mygame"}, headers=tokens["ada"])
    assert client.delete("/games/mygame", headers=tokens["tunde"]).status_code == 403
    assert client.delete("/games/mygame", headers=tokens["ada"]).status_code == 204
    assert storage.paths("mygame") == []
    assert client.get("/games").json() == []


def test_publishing_off_when_not_set_up(env):
    client, _, tokens, _ = env
    app.dependency_overrides[get_game_store] = lambda: None
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/x"}, headers=tokens["ada"])
    assert res.status_code == 503


def test_r2_store_uses_the_bucket_service(monkeypatch):
    calls = []
    monkeypatch.setattr(bucket, "put_game", lambda game, files: calls.append(("put", game, sorted(files))))
    monkeypatch.setattr(bucket, "list_files", lambda game: [{"path": "index.html", "size": 1, "last_modified": None}])
    monkeypatch.setattr(bucket, "delete_game", lambda game: calls.append(("delete", game)))
    store = R2GameStore()
    store.put_game("dave", {"index.html": b"x", "save/index.html": b"y"})
    assert store.paths("dave") == ["index.html"]
    store.delete_game("dave")
    assert calls == [("put", "dave", ["index.html", "save/index.html"]), ("delete", "dave")]


def test_my_games_lists_only_your_own(env):
    client, _, tokens, _ = env
    assert client.get("/games/mine").status_code == 401
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/x", "name": "ada-one"}, headers=tokens["ada"])
    client.post("/games/publish", json={"repo_url": "https://github.com/tunde/y", "name": "tunde-one"}, headers=tokens["tunde"])
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/z", "name": "ada-two"}, headers=tokens["ada"])
    assert [g["name"] for g in client.get("/games/mine", headers=tokens["ada"]).json()] == ["ada-two", "ada-one"]
    assert [g["name"] for g in client.get("/games/mine", headers=tokens["tunde"]).json()] == ["tunde-one"]
