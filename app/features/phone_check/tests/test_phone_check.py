import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.phone_check import browser, files, service
from app.features.phone_check.models import PhoneCheck
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import Game, User
from app.security import create_token

VIEWPORT = b'<meta name="viewport" content="width=device-width, initial-scale=1">'
GAMES = {
    "fits": {"index.html": VIEWPORT + b"<link rel='stylesheet' href='style.css'><canvas width='800'></canvas>",
             "style.css": b"canvas { width: 100%; max-width: 800px } .dialog { width: 600px }"},
    "no-viewport": {"index.html": b"<canvas></canvas><script src='game.js'></script>", "game.js": b"run()"},
    "desktop-viewport": {"index.html": b'<meta name="viewport" content="width=1024"><canvas></canvas>'},
    "wide-body": {"index.html": VIEWPORT + b"<style>body { min-width: 960px }</style><canvas></canvas>"},
    "wide-canvas": {"index.html": VIEWPORT + b"<canvas width='960' height='540'></canvas>"},
    "media-query": {"index.html": VIEWPORT + b"<style>@media (min-width: 900px) { body { width: 960px } }</style><p>hi</p>"},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "phone_check")
    monkeypatch.setattr(settings, "phone_check_browser", False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    store = LocalGameStore(tmp_path)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_game_store] = lambda: store
    monkeypatch.setattr(service, "SessionLocal", Session)
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada", "bo")}
        db.add_all(users.values())
        db.flush()
        for name, game_files in GAMES.items():
            store.put_game(name, game_files)
            db.add(Game(name=name, owner_id=users["ada"].id, repo_url=f"https://github.com/x/{name}"))
        db.add(Game(name="no-page", owner_id=users["ada"].id, repo_url="https://github.com/x/no-page"))
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, Session, store
    app.dependency_overrides.clear()


def codes(store, name, open_in_browser=None):
    status, method, found = service.check(store, name, open_in_browser)
    return status, method, [f["code"] for f in found]


def test_what_the_file_check_finds(env):
    client, tokens, Session, store = env
    assert codes(store, "fits") == ("ok", "files", [])  # a scaled canvas and a wide dialog aren't the page's width
    assert codes(store, "no-viewport") == ("worth_a_look", "files", ["no_viewport"])
    assert codes(store, "desktop-viewport") == ("worth_a_look", "files", ["viewport_width"])
    assert codes(store, "wide-body") == ("worth_a_look", "files", ["fixed_width"])
    assert codes(store, "wide-canvas") == ("worth_a_look", "files", ["wide_canvas"])
    assert codes(store, "media-query") == ("ok", "files", [])  # rules for big screens only already adapt
    assert codes(store, "no-page") == ("error", "files", ["no_page"])


def test_the_browser_decides_about_widths_and_keeps_the_viewport_findings(env):
    client, tokens, Session, store = env
    sideways = lambda url: [{"code": "scrolls_sideways", "message": "At 320 px wide the page is 476 px wide"}]
    fine = lambda url: []
    assert codes(store, "wide-canvas", fine) == ("ok", "browser", [])  # its code scales the canvas after all
    assert codes(store, "fits", sideways) == ("worth_a_look", "browser", ["scrolls_sideways"])
    assert codes(store, "no-viewport", fine) == ("worth_a_look", "browser", ["no_viewport"])
    assert codes(store, "no-viewport", sideways) == ("worth_a_look", "browser", ["no_viewport"])  # the tag is why: told once

    def broken(url):
        raise TimeoutError("page didn't load")
    assert codes(store, "wide-canvas", broken) == ("worth_a_look", "files", ["wide_canvas"])  # the file checks stand


def test_what_a_measurement_means():
    assert browser.findings_from(320, {"vw": 320, "sw": 476, "off": 25})[0]["code"] == "scrolls_sideways"
    assert browser.findings_from(320, {"vw": 320, "sw": 320, "off": 20})[0]["code"] == "off_screen"
    assert browser.findings_from(320, {"vw": 320, "sw": 321, "off": 5}) == []  # a pixel of rounding, a slide-out menu
    assert browser.findings_from(320, {"vw": 900, "sw": 900, "off": 0})[0]["code"] == "scrolls_sideways"  # zoomed out to fit


def test_publishing_queues_a_check_and_the_job_runs_it(env):
    client, tokens, Session, store = env
    events.publish("game_published", {"game": "no-viewport", "owner_id": 2})
    events.publish("game_updated", {"game": "fits", "owner_id": 2, "auto": True})
    events.publish("game_published", {"game": "hangout", "owner_id": 1})  # start.ng's own space: never
    with Session() as db:
        assert sorted(service.due(db)) == ["fits", "no-viewport"]
        assert service.check_due(db, store) == 2
        assert db.get(PhoneCheck, "no-viewport").status == "worth_a_look"
        assert db.get(PhoneCheck, "fits").status == "ok"
        assert service.due(db) == []


def test_only_the_owner_sees_and_rechecks_their_game(env):
    client, tokens, Session, store = env
    assert client.get("/games/no-viewport/phone-check", headers=tokens["ada"]).json()["status"] == "unchecked"
    assert client.get("/games/no-viewport/phone-check", headers=tokens["bo"]).status_code == 403
    assert client.post("/games/no-viewport/phone-check", headers=tokens["bo"]).status_code == 403
    assert client.post("/games/no-viewport/phone-check", headers=tokens["ada"]).json()["status"] == "unchecked"
    with Session() as db:
        service.check_due(db, store)
    body = client.get("/games/no-viewport/phone-check", headers=tokens["ada"]).json()
    assert body["status"] == "worth_a_look" and body["method"] == "files"
    assert body["findings"][0]["code"] == "no_viewport" and "width=device-width" in body["findings"][0]["message"]
    assert client.post("/games/no-viewport/phone-check", headers=tokens["ada"]).json()["status"] == "worth_a_look"  # just checked


def test_admins_see_games_worth_a_look_and_can_check_every_game(env):
    client, tokens, Session, store = env
    assert client.get("/admin/phone-checks", headers=tokens["ada"]).status_code == 404  # admin pages hide from players
    queued = client.post("/admin/phone-checks/scan", headers=tokens["boss"]).json()["queued"]
    assert queued == len(GAMES) + 1
    with Session() as db:
        while service.check_due(db, store):
            pass
    body = client.get("/admin/phone-checks", headers=tokens["boss"]).json()
    assert body["counts"] == {"ok": 2, "worth_a_look": 4, "error": 1}
    assert sorted(g["game"] for g in body["games"]) == ["desktop-viewport", "no-viewport", "wide-body", "wide-canvas"]
    assert body["games"][0]["owner"] == "Ada"


def test_ships_dark(env, monkeypatch):
    client, tokens, Session, store = env
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/games/fits/phone-check", headers=tokens["ada"]).status_code == 404
    events.publish("game_published", {"game": "fits", "owner_id": 2})
    with Session() as db:
        assert service.due(db) == []


def test_file_check_reads_linked_stylesheets_only_from_the_game():
    pages = {"index.html": VIEWPORT + b"<link rel='stylesheet' href='https://cdn.example.com/x.css'><link rel='stylesheet' href='css/a.css'>",
             "css/a.css": b"html { width: 1200px }"}
    assert [f["code"] for f in files.check(pages.get)] == ["fixed_width"]
