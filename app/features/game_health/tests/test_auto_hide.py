"""F8: an unhealthy game is hidden through "needs fixing", its owner and the super admins are emailed, and nothing is
hidden when many games fail at once."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import storage
from app.config import settings
from app.db import Base, get_db
from app.features.email.models import Email
from app.features.game_health import auto_hide, service
from app.features.game_health.models import GameHealth
from app.features.needs_fixing.models import FixRequest
from app.game_store import LocalGameStore, get_game_store
from app.main import app
from app.models import Activity, Game, User
from app.security import create_token

GOOD = b"<!doctype html><canvas></canvas><script src='game.js'></script>"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com,sam@example.com")
    monkeypatch.setattr(settings, "features", "game_health,needs_fixing,email")
    monkeypatch.setattr(settings, "game_health_http", False)
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
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada")}
        users["pat"] = User(email="pat@example.com", name="Pat", password_hash="x")  # signed up with a password
        db.add_all(users.values())
        db.flush()
        for i in range(8):  # healthy games, so two broken ones aren't "many at once"
            add(db, store, f"fine-{i}", users["ada"], {"index.html": GOOD, "game.js": b"run()"})
        add(db, store, "broken", users["ada"], {"index.html": GOOD})  # game.js is missing
        add(db, store, "pats-game", users["pat"], {"index.html": GOOD})
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
    yield TestClient(app), tokens, Session, store
    app.dependency_overrides.clear()


def add(db, store, name, owner, files):
    store.put_game(name, files)
    db.add(Game(name=name, owner_id=owner.id, repo_url=f"https://github.com/x/{name}"))


def rounds(Session, store, n=service.FAILS_IN_A_ROW, fetch=service.fetch_status):
    """n rounds of the background checks, each one checking every listed game."""
    for _ in range(n):
        with Session() as db:
            db.execute(update(GameHealth).values(checked_at=None))
            db.commit()
            service.check_due(db, store, fetch)


def mail(Session):
    with Session() as db:
        return sorted((r.to_email, r.notes[0]["kind"]) for r in db.scalars(select(Email)).all())


def offline(store, name):
    return (store.root / storage.HIDDEN_PREFIX / name).exists()


def test_an_unhealthy_game_is_hidden_and_people_are_told(env):
    client, tokens, Session, store = env
    rounds(Session, store, 2)
    with Session() as db:
        assert db.get(Game, "broken").hidden is None  # failing twice isn't enough
    rounds(Session, store, 1)
    with Session() as db:
        assert db.get(Game, "broken").hidden == "needs_fixing" and db.get(Game, "pats-game").hidden == "needs_fixing"
        r = db.scalar(select(FixRequest).where(FixRequest.game_name == "broken"))
        assert r.created_by == auto_hide.HIDDEN_BY and "game.js" in r.note
        assert db.get(GameHealth, "broken").failures == 0  # 3 new failures before it's hidden again
        assert len(db.scalars(select(Activity).where(Activity.action == "game_health.auto_hidden")).all()) == 2
    assert offline(store, "broken") and not offline(store, "fine-0")
    # the owner (Pat signed up with a password, so their address isn't checked: no email), and both super admins
    assert mail(Session) == [("ada@example.com", "game_hidden_unhealthy")] + \
        [("boss@example.com", "admin_game_hidden_unhealthy")] * 2 + [("sam@example.com", "admin_game_hidden_unhealthy")] * 2
    # the owner sees it in Your games as start.ng's own mark, and says it's fixed; an admin puts it back
    mine = client.get("/needs-fixing/mine", headers=tokens["ada"]).json()
    assert [(m["game"], m["automatic"]) for m in mine] == [("broken", True)]
    store.put_file("broken", "game.js", b"run()", "text/javascript")
    client.post(f"/needs-fixing/{mine[0]['id']}/fixed", json={"note": "added game.js"}, headers=tokens["ada"])
    assert client.post(f"/admin/needs-fixing/{mine[0]['id']}/approve", headers=tokens["boss"]).status_code == 200
    rounds(Session, store, 1)
    with Session() as db:
        assert db.get(Game, "broken").hidden is None and db.get(GameHealth, "broken").status == "ok"


def test_the_email_rendering(env):
    note = auto_hide.render_owner({"game": "lane-dash", "problems": [{"code": "no_page", "message": "<index> missing"}]})
    assert note.subject == "Lane Dash is hidden: start.ng couldn't open it"
    assert "&lt;index&gt; missing" in note.html and "/?profile" in note.text


def test_developer_emails_can_be_switched_off(env, monkeypatch):
    client, tokens, Session, store = env
    monkeypatch.setattr(settings, "dev_emails", False)
    rounds(Session, store)
    assert {to for to, _ in mail(Session)} == {"boss@example.com", "sam@example.com"}  # admins still told


def test_nothing_is_hidden_when_many_games_fail_at_once(env):
    client, tokens, Session, store = env
    for i in range(4):  # 6 of 12 games broken: looks like start.ng is down
        store.delete_file(f"fine-{i}", "game.js")
    rounds(Session, store)
    rounds(Session, store, 2)  # still failing: the alert isn't sent again within the hour
    with Session() as db:
        assert db.scalars(select(Game.name).where(Game.hidden.is_not(None))).all() == []
        assert db.get(GameHealth, "broken").status == "unhealthy"
    assert mail(Session) == [("boss@example.com", "admin_health_outage"), ("sam@example.com", "admin_health_outage")]


def test_no_hiding_without_needs_fixing_or_the_setting(env, monkeypatch):
    client, tokens, Session, store = env
    monkeypatch.setattr(settings, "features", "game_health,email")
    rounds(Session, store)
    monkeypatch.setattr(settings, "features", "game_health,needs_fixing,email")
    monkeypatch.setattr(settings, "game_health_auto_hide", False)
    rounds(Session, store)
    with Session() as db:
        assert db.get(Game, "broken").hidden is None and db.get(GameHealth, "broken").status == "unhealthy"
    assert mail(Session) == []


def test_check_now_hides_too_and_a_hidden_game_is_checked_without_its_address(env, monkeypatch):
    client, tokens, Session, store = env
    for _ in range(service.FAILS_IN_A_ROW):
        r = client.post("/admin/game-health/games/broken/check", headers=tokens["boss"]).json()
    with Session() as db:
        assert db.get(Game, "broken").hidden == "needs_fixing"
    # its address answers with an error page on purpose now, so only its files are checked
    monkeypatch.setattr(settings, "game_health_http", True)
    store.put_file("broken", "game.js", b"run()", "text/javascript")
    with Session() as db:
        row = service.check_and_record(db, store, "broken", fetch=lambda url: 404)
        assert row.status == "ok"


def test_an_outage_seen_over_the_last_20_minutes_holds_even_when_a_round_checks_one_game(env):
    """In production a round only checks the games that are due, often just a few. If most games failed their last
    check within CHECK_EVERY, it's start.ng that's down: nothing is hidden."""
    client, tokens, Session, store = env
    now = service.now()
    with Session() as db:
        for i in range(8):  # every healthy game failed its check a few minutes ago
            db.add(GameHealth(game_name=f"fine-{i}", status="failing", failures=1, checked_at=now, problems=[]))
        db.add(GameHealth(game_name="broken", status="unhealthy", failures=3, checked_at=now,
                          problems=[{"code": "missing_file", "message": "game.js is missing"}]))
        db.commit()
        assert auto_hide.act(db, store, ["broken"], checked=1, failed=1) == []
        assert db.get(Game, "broken").hidden is None
    assert mail(Session) == [("boss@example.com", "admin_health_outage"), ("sam@example.com", "admin_health_outage")]
    # once the others pass again, the broken one is hidden on its next check
    with Session() as db:
        for i in range(8):
            db.get(GameHealth, f"fine-{i}").status = "ok"
        db.commit()
        assert auto_hide.act(db, store, ["broken"], checked=1, failed=1) == ["broken"]
def test_a_game_waiting_for_review_is_checked_but_never_auto_hidden(env, monkeypatch):
    client, tokens, Session, store = env
    with Session() as db:
        db.get(Game, "broken").hidden = "in_review"  # F2: unlisted, but its address works
        db.commit()
    opened = []
    monkeypatch.setattr(settings, "game_health_http", True)
    rounds(Session, store, fetch=lambda url: opened.append(url) or 200)
    with Session() as db:
        assert db.get(GameHealth, "broken").status == "unhealthy"  # the review card shows it
        assert db.get(Game, "broken").hidden == "in_review"  # left to the reviewer
    assert any("broken" in u for u in opened)
    with Session() as db:
        assert db.get(Game, "pats-game").hidden == "needs_fixing"  # a live broken game is, though
