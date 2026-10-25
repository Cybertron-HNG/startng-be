"""F10: problem reports about start.ng, sorted by admins; a real bug pays the reporter coins once."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.support import service
from app.features.wardrobe import service as wardrobe
from app.main import app
from app.models import User
from app.security import create_token


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(settings, "features", "support,wardrobe")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {k: User(email=f"{k}@example.com", name=k.title(), google_sub=f"g-{k}") for k in ("boss", "ada", "tunde")}
        db.add_all(users.values())
        db.commit()
        tokens = {k: {"Authorization": f"Bearer {create_token(u.id)}"} for k, u in users.items()}
        ids = {k: u.id for k, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def send(client, tokens, who="ada", what="The leaderboard page stays blank after I sign in", page="/games/lane-dash?code=secret#top"):
    return client.post("/support/reports", json={"what": what, "page": page, "browser": "Chrome 130 on Windows"}, headers=tokens[who])


def decide(client, tokens, report_id, status, **extra):
    return client.post(f"/admin/support/reports/{report_id}", json={"status": status, **extra}, headers=tokens["boss"])


def test_signed_in_players_report_problems(env, monkeypatch):
    client, tokens, ids, Session = env
    assert client.post("/support/reports", json={"what": "Something broke on the page"}).status_code == 401
    assert send(client, tokens, what="broken").status_code == 400  # too short to act on
    r = send(client, tokens).json()
    assert (r["status"], r["page"], r["coins"]) == ("new", "/games/lane-dash", 0)  # no query string or fragment kept
    assert [m["id"] for m in client.get("/support/reports/mine", headers=tokens["ada"]).json()] == [r["id"]]
    assert client.get("/support/reports/mine", headers=tokens["tunde"]).json() == []
    monkeypatch.setattr(settings, "support_reports_per_day", 2)
    send(client, tokens, what="Another thing: the share button does nothing on phones")
    too_many = send(client, tokens, what="A third thing that also went wrong today")
    assert too_many.status_code == 400 and too_many.json()["errors"][0]["code"] == "too_many"


def test_admins_see_look_alikes(env):
    client, tokens, ids, Session = env
    first = send(client, tokens).json()["id"]
    other = send(client, tokens, who="tunde", what="Coins in the shop show the wrong price", page="/shop").json()["id"]
    again = send(client, tokens, who="tunde", what="After signing in, the leaderboard page is blank").json()["id"]
    assert client.get("/admin/support/reports", headers=tokens["ada"]).status_code == 404  # admins only
    queue = client.get("/admin/support/reports", headers=tokens["boss"]).json()
    by_id = {r["id"]: r for r in queue["reports"]}
    assert [s["id"] for s in by_id[again]["similar"]] == [first]
    assert by_id[other]["similar"] == [] and by_id[first]["reporter_email"] == "ada@example.com"
    assert (queue["counts"], queue["reward"], queue["per_day"]) == ({"new": 3}, 50, 3)


def test_a_real_bug_pays_once(env):
    client, tokens, ids, Session = env
    rid = send(client, tokens).json()["id"]
    r = decide(client, tokens, rid, "confirmed", note="Thanks, we found it.").json()
    assert (r["status"], r["coins"], r["admin_note"]) == ("confirmed", 50, "Thanks, we found it.")
    assert decide(client, tokens, rid, "confirmed").json()["coins"] == 50  # confirming again pays nothing more
    assert decide(client, tokens, rid, "fixed").json()["status"] == "fixed"
    with Session() as db:
        assert wardrobe.balance(db, ids["ada"]) == 50
    mine = client.get("/support/reports/mine", headers=tokens["ada"]).json()[0]
    assert (mine["status"], mine["coins"], mine["admin_note"]) == ("fixed", 50, "Thanks, we found it.")


def test_at_most_three_paid_reports_a_day(env, monkeypatch):
    client, tokens, ids, Session = env
    monkeypatch.setattr(settings, "support_reports_per_day", 10)
    rids = [send(client, tokens, what=f"Problem number {i}: the {w} button does nothing").json()["id"]
            for i, w in enumerate(["play", "share", "rate", "follow"])]
    paid = [decide(client, tokens, rid, "confirmed").json()["coins"] for rid in rids]
    assert paid == [50, 50, 50, 0]
    with Session() as db:
        assert wardrobe.balance(db, ids["ada"]) == 150


def test_duplicates_and_not_bugs_pay_nothing(env, monkeypatch):
    client, tokens, ids, Session = env
    first = send(client, tokens).json()["id"]
    second = send(client, tokens, who="tunde", what="Blank leaderboard page after sign in").json()["id"]
    assert decide(client, tokens, second, "duplicate").status_code == 400  # of which one?
    r = decide(client, tokens, second, "duplicate", duplicate_of=first).json()
    assert (r["status"], r["duplicate_of"], r["coins"]) == ("duplicate", first, 0)
    assert decide(client, tokens, first, "fixed").status_code == 400  # confirm it first
    assert decide(client, tokens, first, "not_a_bug").json()["coins"] == 0
    assert decide(client, tokens, first, "maybe").status_code == 400
    # with the wardrobe off there's no wallet: a confirmed bug is recorded, unpaid
    monkeypatch.setattr(settings, "features", "support")
    third = send(client, tokens, what="The tournament countdown shows a negative time").json()["id"]
    assert decide(client, tokens, third, "confirmed").json()["coins"] == 0


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, ids, Session = env
    monkeypatch.setattr(settings, "features", "")
    assert send(client, tokens).status_code == 404
    assert client.get("/admin/support/reports", headers=tokens["boss"]).status_code == 404


def test_words():
    assert service.likeness("The leaderboard page stays blank", "Blank leaderboard after sign in") > service.SIMILAR
    assert service.clean_page("https://start.ng/games/x?token=abc#y") == "/games/x"
