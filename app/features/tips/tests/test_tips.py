import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.progress.models import GameOpen
from app.features.wardrobe import service as wardrobe
from app.features.wardrobe.models import CoinEntry
from app.main import app
from app.models import Game, User
from app.security import create_token


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(settings, "features", "tips,wardrobe")
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("daniel", "ada", "tunde")}
        db.add_all(users.values())
        db.flush()
        db.add_all([
            Game(name="coin-rush", owner_id=users["daniel"].id, repo_url="https://github.com/daniel/coin-rush"),
            Game(name="ada-run", owner_id=users["ada"].id, repo_url="https://github.com/ada/ada-run"),
            GameOpen(user_id=users["ada"].id, game_name="coin-rush"),  # Ada has played Coin Rush
            GameOpen(user_id=users["daniel"].id, game_name="ada-run"),
        ])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def earn(Session, user_id, amount, reason="first_run"):
    with Session() as db:
        wardrobe.pay(db, user_id, amount, reason, f"test:{reason}:{amount}")


def test_a_tip_moves_earned_coins_to_the_creator(env):
    client, tokens, ids, Session = env
    earn(Session, ids["ada"], 80)
    r = client.post("/games/coin-rush/tips", json={"amount": 25, "note": "Love   this game!"}, headers=tokens["ada"])
    assert r.status_code == 201 and r.json() == {"balance": 55, "supporters": 1}
    with Session() as db:
        assert wardrobe.balance(db, ids["daniel"]) == 25
        rows = db.scalars(select(CoinEntry).where(CoinEntry.reason.in_(("tip_out", "tip_in")))).all()
        assert sorted((e.user_id, e.amount) for e in rows) == sorted([(ids["ada"], -25), (ids["daniel"], 25)])
    got = client.get("/games/coin-rush/tips/received", headers=tokens["daniel"]).json()
    assert got["count"] == 1 and got["coins"] == 25 and got["tips"][0]["by"] == "Ada" and got["tips"][0]["note"] == "Love this game!"
    assert client.get("/games/coin-rush/tips/received", headers=tokens["ada"]).status_code == 404  # creators only


def test_the_box_says_who_can_tip(env):
    client, tokens, ids, Session = env
    signed_out = client.get("/games/coin-rush/tips").json()
    assert signed_out == {"supporters": 0, "amounts": [10, 25, 50], "can_tip": None, "reason": None, "coins": None}
    assert client.get("/games/coin-rush/tips", headers=tokens["daniel"]).json()["reason"] == "You can't tip yourself"
    assert "Play the game first" in client.get("/games/coin-rush/tips", headers=tokens["tunde"]).json()["reason"]
    assert "at least 10 coins" in client.get("/games/coin-rush/tips", headers=tokens["ada"]).json()["reason"]
    earn(Session, ids["ada"], 30)
    box = client.get("/games/coin-rush/tips", headers=tokens["ada"]).json()
    assert box["can_tip"] is True and box["coins"] == 30


def test_refused_tips(env):
    client, tokens, ids, Session = env
    earn(Session, ids["ada"], 20)
    earn(Session, ids["tunde"], 100)
    assert client.post("/games/coin-rush/tips", json={"amount": 15}, headers=tokens["ada"]).status_code == 422
    assert client.post("/games/coin-rush/tips", json={"amount": 25}, headers=tokens["ada"]).json()["detail"] == "You need 5 more coins"
    assert client.post("/games/ada-run/tips", json={"amount": 10}, headers=tokens["ada"]).status_code == 409  # own game
    assert client.post("/games/coin-rush/tips", json={"amount": 10}, headers=tokens["tunde"]).status_code == 409  # not played
    assert client.post("/games/coin-rush/tips", json={"amount": 10}).status_code == 401
    assert client.post("/games/nope/tips", json={"amount": 10}, headers=tokens["ada"]).status_code == 404
    with Session() as db:
        assert wardrobe.balance(db, ids["ada"]) == 20  # nothing moved


def test_daily_limits(env):
    client, tokens, ids, Session = env
    earn(Session, ids["ada"], 300)
    send = lambda amount: client.post("/games/coin-rush/tips", json={"amount": amount}, headers=tokens["ada"])
    assert send(50).status_code == 201 and send(50).status_code == 201
    over = send(10)  # 100 coins sent today already
    assert over.status_code == 409 and "100 coins" in over.json()["detail"]
    # three tips a day, even small ones
    earn(Session, ids["daniel"], 200)
    tip = lambda: client.post("/games/ada-run/tips", json={"amount": 10}, headers=tokens["daniel"])
    assert [tip().status_code for _ in range(4)] == [201, 201, 201, 409]


def test_coins_from_admins_and_tips_received_cannot_be_passed_on(env):
    client, tokens, ids, Session = env
    with Session() as db:
        wardrobe.adjust(db, ids["ada"], 500, "gift")  # an admin gift
    r = client.post("/games/coin-rush/tips", json={"amount": 10}, headers=tokens["ada"])
    assert r.status_code == 409 and r.json()["detail"] == "You need 10 more coins"
    earn(Session, ids["ada"], 50)
    assert client.post("/games/coin-rush/tips", json={"amount": 50}, headers=tokens["ada"]).status_code == 201
    # Daniel got 50 in tips: he can spend them in the shop, but not tip them on
    assert client.get("/games/ada-run/tips", headers=tokens["daniel"]).json()["coins"] == 0


def test_notes_go_through_the_name_filter(env, monkeypatch):
    client, tokens, ids, Session = env
    from app.features.moderation import service as moderation
    monkeypatch.setattr(moderation, "check_text", lambda text, db=None: "badword" if "badword" in (text or "") else None)
    earn(Session, ids["ada"], 50)
    r = client.post("/games/coin-rush/tips", json={"amount": 10, "note": "you badword"}, headers=tokens["ada"])
    assert r.status_code == 422
    with Session() as db:
        assert wardrobe.balance(db, ids["ada"]) == 50
