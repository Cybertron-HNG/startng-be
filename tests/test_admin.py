from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.config import settings
from app.db import Base, get_db
from app.game_store import get_game_store
from app.main import app
from app.models import Activity, Game, GameReport, Tournament, User
from app.security import create_token

NOON = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)  # 12:00 in Nigeria
BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]


class FakeStore:
    def __init__(self):
        self.deleted = []
        self.offline = set()  # games whose own address shows an error page

    def delete_game(self, name):
        self.deleted.append(name)
        self.offline.discard(name)

    def set_hidden(self, name, hidden):
        (self.offline.add if hidden else self.offline.discard)(name)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(tournaments, "now", lambda: NOON)
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com, Second@Example.com")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    store = FakeStore()

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_game_store] = lambda: store
    with Session() as db:
        users = {
            "boss": User(email="boss@example.com", name="Boss", google_sub="g-boss"),
            "mod": User(email="mod@example.com", name="Mod", google_sub="g-mod"),
            "player": User(email="player@example.com", name="Player", google_sub="g-player"),
            "dev": User(email="dev@example.com", name="Dev", google_sub="g-dev"),
            # Registered with a password, never proved they own the email: no admin access even though it's on the list
            "fake": User(email="second@example.com", name="Fake", password_hash="x"),
        }
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name=f"g{i}", owner_id=users["dev"].id, repo_url=f"https://github.com/dev/g{i}", leaderboard=BOARD)
                    for i in range(6)])
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
        ids = {k: u.id for k, u in users.items()}
    yield TestClient(app), tokens, ids, Session, store
    app.dependency_overrides.clear()


def make_mod(client, tokens, permissions):
    role = client.post("/admin/roles", json={"name": "Moderator", "permissions": permissions}, headers=tokens["boss"]).json()
    r = client.post("/admin/admins", json={"email": "MOD@example.com", "role_id": role["id"]}, headers=tokens["boss"])
    assert r.status_code == 201, r.text
    return role


def test_only_admins_see_the_admin_api(env):
    client, tokens, *_ = env
    me = client.get("/admin/me", headers=tokens["boss"]).json()
    assert me["is_super"] and "developers.delete" in me["permissions"]
    for who in ("player", "fake", "mod"):  # mod isn't an admin yet
        assert client.get("/admin/me", headers=tokens[who]).status_code == 404
    assert client.get("/admin/me").status_code == 401


def test_super_admin_makes_roles_and_adds_admins(env):
    client, tokens, *_ = env
    role = make_mod(client, tokens, ["reports.view", "games.flag"])
    me = client.get("/admin/me", headers=tokens["mod"]).json()
    assert me == {"email": "mod@example.com", "is_super": False, "role": "Moderator", "permissions": ["games.flag", "reports.view"]}
    # Moderators can't manage admins or roles, or do what their role doesn't allow
    assert client.get("/admin/admins", headers=tokens["mod"]).status_code == 403
    assert client.post("/admin/roles", json={"name": "X", "permissions": []}, headers=tokens["mod"]).status_code == 403
    assert client.get("/admin/log", headers=tokens["mod"]).status_code == 403
    # Unknown permissions and duplicate names are refused
    assert client.post("/admin/roles", json={"name": "Bad", "permissions": ["everything"]}, headers=tokens["boss"]).status_code == 422
    assert client.post("/admin/roles", json={"name": "moderator", "permissions": []}, headers=tokens["boss"]).status_code == 409
    admins = client.get("/admin/admins", headers=tokens["boss"]).json()
    assert [a["email"] for a in admins] == ["boss@example.com", "second@example.com", "mod@example.com"]
    assert admins[1]["signed_up"] is False and admins[2]["role"] == "Moderator"
    # Changing the role changes what they can do straight away
    client.put(f"/admin/roles/{role['id']}", json={"name": "Moderator", "permissions": ["log.view"]}, headers=tokens["boss"])
    assert client.get("/admin/log", headers=tokens["mod"]).status_code == 200
    # Removed: no access
    client.delete(f"/admin/admins/{admins[2]['id']}", headers=tokens["boss"])
    assert client.get("/admin/me", headers=tokens["mod"]).status_code == 404


def test_a_report_waits_for_an_admin_before_the_game_is_hidden(env):
    client, tokens, ids, Session, store = env
    assert client.post("/games/g1/report", json={"reason": "broken"}).status_code == 401
    assert client.post("/games/g1/report", json={"reason": "other"}, headers=tokens["player"]).status_code == 422
    r = client.post("/games/g1/report", json={"reason": "obscene", "details": "level 2"}, headers=tokens["player"])
    assert r.status_code == 201 and r.json()["reporter_id"] == ids["player"] and r.json()["status"] == "open"
    again = client.post("/games/g1/report", json={"reason": "broken"}, headers=tokens["player"])
    assert again.json()["id"] == r.json()["id"]  # one open report per player per game
    # Still up while it waits
    assert "g1" in [g["name"] for g in client.get("/games").json()]
    review = client.get("/admin/games/review", headers=tokens["boss"]).json()
    assert [(g["name"], g["hidden"], g["open_reports"], g["reports"][0]["reporter"]) for g in review] == [("g1", None, 1, "Player")]
    # Approved: hidden
    assert store.offline == set()
    closed = client.post(f"/admin/reports/{r.json()['id']}/close", json={"outcome": "approved"}, headers=tokens["boss"]).json()
    assert store.offline == {"g1"}  # its own address is offline too
    assert closed["status"] == "approved" and closed["closed_by"] == "boss@example.com"
    assert client.post(f"/admin/reports/{r.json()['id']}/close", json={"outcome": "dismissed"}, headers=tokens["boss"]).status_code == 409
    assert "g1" not in [g["name"] for g in client.get("/games").json()]
    mine = {g["name"]: g for g in client.get("/games/mine", headers=tokens["dev"]).json()}
    assert mine["g1"]["hidden"] == "reported"
    assert client.post("/admin/games/g1/restore", json={"note": "fixed"}, headers=tokens["boss"]).status_code == 204
    assert "g1" in [g["name"] for g in client.get("/games").json()] and store.offline == set()
    with Session() as db:
        assert db.scalars(select(Activity.action).order_by(Activity.id)).all() == ["game.reported", "report.approved", "game.restored"]
        assert db.scalars(select(Activity.kind).order_by(Activity.id)).all() == ["player", "admin", "admin"]


def test_a_dismissed_report_leaves_the_game_up(env):
    client, tokens, *_ = env
    rid = client.post("/games/g3/report", json={"reason": "broken"}, headers=tokens["player"]).json()["id"]
    client.post(f"/admin/reports/{rid}/close", json={"outcome": "dismissed", "note": "works for me"}, headers=tokens["boss"])
    assert "g3" in [g["name"] for g in client.get("/games").json()]
    assert client.get("/admin/games/review", headers=tokens["boss"]).json() == []


def test_reports_per_day_are_limited(env, monkeypatch):
    client, tokens, *_ = env
    monkeypatch.setattr(settings, "reports_per_day", 2)
    assert client.post("/games/g1/report", json={"reason": "broken"}, headers=tokens["player"]).status_code == 201
    assert client.post("/games/g2/report", json={"reason": "broken"}, headers=tokens["player"]).status_code == 201
    assert client.post("/games/g3/report", json={"reason": "broken"}, headers=tokens["player"]).status_code == 429


def test_admin_flags_hide_at_once(env):
    client, tokens, *_ = env
    make_mod(client, tokens, ["games.flag", "reports.view"])
    flag = client.post("/admin/games/g2/flag", json={"reason": "harmful"}, headers=tokens["mod"])
    assert flag.status_code == 201 and flag.json()["by_admin"] and flag.json()["status"] == "approved"
    assert client.get("/admin/games/review", headers=tokens["mod"]).json()[0]["hidden"] == "flagged"
    # A player's report on it waits; the moderator can't decide it without reports.close
    rid = client.post("/games/g2/report", json={"reason": "broken"}, headers=tokens["player"]).json()["id"]
    assert client.post(f"/admin/reports/{rid}/close", json={"outcome": "approved"}, headers=tokens["mod"]).status_code == 403
    client.post(f"/admin/reports/{rid}/close", json={"outcome": "approved"}, headers=tokens["boss"])
    assert client.get("/admin/games/review", headers=tokens["mod"]).json()[0]["hidden"] == "flagged"  # not weakened
    assert len(client.get("/admin/reports?state=approved", headers=tokens["mod"]).json()) == 2


def test_activity_log_covers_the_whole_site(env):
    client, tokens, ids, *_ = env
    client.post("/auth/register", json={"email": "new@example.com", "password": "password123"})
    client.post("/games/g1/report", json={"reason": "broken"}, headers=tokens["player"])
    client.get("/tournaments/current")
    log = client.get("/admin/log", headers=tokens["boss"]).json()
    assert [(e["kind"], e["action"]) for e in log][:3] == [("system", "tournament.created"), ("player", "game.reported"), ("player", "account.created")]
    assert {e["actor"] for e in client.get("/admin/log?kind=player", headers=tokens["boss"]).json()} == {"new@example.com", "player@example.com"}


def test_blocking_and_deleting_need_three_contacts(env):
    client, tokens, ids, Session, store = env
    dev = ids["dev"]
    assert client.post(f"/admin/developers/{dev}/block", headers=tokens["boss"]).status_code == 409
    for issue in ("broken menu", "obscene art", "stolen sprites"):
        client.post(f"/admin/developers/{dev}/contacts", json={"issue": issue, "game_name": "g1"}, headers=tokens["boss"])
    out = client.post(f"/admin/developers/{dev}/block", headers=tokens["boss"]).json()
    assert out["blocked_at"] and out["can_block"] and len(out["contacts"]) == 3
    assert store.offline == {f"g{i}" for i in range(6)}
    assert client.get("/games").json() == []
    assert client.get("/auth/me", headers=tokens["dev"]).status_code == 401
    # Admins are never blocked
    assert client.post(f"/admin/developers/{ids['boss']}/block", headers=tokens["boss"]).status_code == 409
    client.post(f"/admin/developers/{dev}/unblock", headers=tokens["boss"])
    assert len(client.get("/games").json()) == 6 and store.offline == set()
    assert client.delete(f"/admin/developers/{dev}", headers=tokens["boss"]).status_code == 204
    assert sorted(store.deleted) == [f"g{i}" for i in range(6)]
    with Session() as db:
        assert db.get(User, dev) is None and db.scalars(select(Game)).all() == []


def test_admins_see_the_current_and_next_tournament(env):
    client, tokens, *_ = env
    make_mod(client, tokens, ["tournaments.view"])
    out = client.get("/admin/tournaments", headers=tokens["mod"]).json()
    current, nxt = out["tournaments"]
    assert current["started"] and not nxt["started"]
    assert nxt["starts_at"].startswith("2026-10-20T17:00") and len(nxt["games"]) == 5 and nxt["games"][0]["by"] == "Dev"
    # Players still only see the current one, and when the next one starts (not its games)
    public = client.get("/tournaments/current").json()
    assert public["id"] == current["id"] and public["next_starts_at"].startswith("2026-10-20T17:00")
    assert client.get("/admin/tournaments", headers=tokens["mod"]).json()["tournaments"][1]["id"] == nxt["id"]  # made once
    assert client.put(f"/admin/tournaments/{nxt['id']}/games", json={"games": ["g0", "g1", "g2"]}, headers=tokens["mod"]).status_code == 403
    # Editing: only before it starts, only eligible games, 3 to 5 of them
    assert client.put(f"/admin/tournaments/{current['id']}/games", json={"games": ["g0", "g1", "g2"]}, headers=tokens["boss"]).status_code == 409
    assert client.put(f"/admin/tournaments/{nxt['id']}/games", json={"games": ["g0", "g1"]}, headers=tokens["boss"]).status_code == 422
    assert client.put(f"/admin/tournaments/{nxt['id']}/games", json={"games": ["g0", "g1", "nope"]}, headers=tokens["boss"]).status_code == 422
    out = client.put(f"/admin/tournaments/{nxt['id']}/games", json={"games": ["g5", "g4", "g3"]}, headers=tokens["boss"]).json()
    assert [g["name"] for g in out["tournaments"][1]["games"]] == ["g5", "g4", "g3"]
    assert {g["name"] for g in out["eligible"]} == {f"g{i}" for i in range(6)} and "cover_url" in out["eligible"][0]
    # A game hidden later is swapped out of tournaments that haven't started
    client.post("/admin/games/g4/flag", json={"reason": "broken"}, headers=tokens["boss"])
    games = [g["name"] for g in client.get("/admin/tournaments", headers=tokens["boss"]).json()["tournaments"][1]["games"]]
    assert "g4" not in games and len(games) == 3 and games[:2] == ["g5", "g3"]


def test_hidden_game_page_is_not_found(env, monkeypatch):
    client, tokens, *_ = env
    from app.routers import pages
    monkeypatch.setattr(pages, "_get_shell", lambda: "<html><head><title>x</title></head><body><div id=\"root\"></div></body></html>")
    assert client.get("/pages/games/g1").status_code == 200
    client.post("/admin/games/g1/flag", json={"reason": "broken"}, headers=tokens["boss"])
    assert client.get("/pages/games/g1").status_code == 404
    assert "/games/g1<" not in client.get("/pages/sitemap.xml").text


def test_local_store_marks_hidden_games_outside_their_folders(tmp_path):
    from app.game_store import LocalGameStore
    store = LocalGameStore(tmp_path)
    store.put_game("g1", {"index.html": b"hi"})
    store.set_hidden("g1", True)
    assert (tmp_path / "_hidden" / "g1").exists() and store.paths("g1") == ["index.html"]
    store.set_hidden("g1", False)
    assert not (tmp_path / "_hidden" / "g1").exists()
    store.set_hidden("g1", True)
    store.delete_game("g1")
    assert not (tmp_path / "_hidden" / "g1").exists()


def test_a_hidden_game_leaves_the_running_tournament(env):
    client, tokens, *_ = env
    current = client.get("/tournaments/current").json()
    game = current["games"][0]["name"]
    client.post(f"/admin/games/{game}/flag", json={"reason": "broken"}, headers=tokens["boss"])
    after = client.get("/tournaments/current").json()
    assert after["id"] == current["id"] and game not in [g["name"] for g in after["games"]]
    assert len(after["games"]) == len(current["games"]) - 1  # no new game joins halfway through the day
    admin_view = client.get("/admin/tournaments", headers=tokens["boss"]).json()["tournaments"][0]
    assert game not in [g["name"] for g in admin_view["games"]]
