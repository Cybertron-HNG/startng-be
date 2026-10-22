from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.progress import service as progress
from app.features.progress.models import Run
from app.features.spotlight import service
from app.features.spotlight.models import ChallengeCompletion, DailyChallenge
from app.main import app
from app.models import Game, User
from app.security import create_token

HIGH = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
TIME = [{"key": "time", "label": "Time", "order": "lowest", "format": "time"}]
PLAYERS = ("boss", "ada", "tunde", "chioma", "musa")


def ago(days=0, minutes=0):
    return datetime.now(timezone.utc) - timedelta(days=days, minutes=minutes)


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

    monkeypatch.setattr(settings, "features", "spotlight,progress")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(service, "SessionLocal", Session)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in PLAYERS}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name="relic", owner_id=users["ada"].id, repo_url="a", leaderboard=HIGH),
                    Game(name="speed", owner_id=users["ada"].id, repo_url="b", leaderboard=TIME)])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def history(Session, ids, game="relic", key="score", values=None):
    """Twelve recent runs by four players: the data a goal is worked out from."""
    values = values or [100 * n for n in range(1, 13)]
    who = [ids[n] for n in ("ada", "tunde", "chioma", "musa")]
    with Session() as db:
        for i, v in enumerate(values):
            db.add(Run(user_id=who[i % 4], game_name=game, score=v, stats={key: v}, created_at=ago(days=1 + i % 9)))
        db.commit()


def play(Session, user_id, value, game="relic", key="score", flagged=False, created=None):
    with Session() as db:
        r = Run(user_id=user_id, game_name=game, score=value, stats={key: value}, flagged=flagged, created_at=created or ago())
        db.add(r)
        db.commit()
        events.publish("run_recorded", {"user_id": user_id, "game": game, "run_id": r.id})
        return r.id


def test_the_goal_comes_from_recent_runs_and_is_reachable(env):
    client, tokens, ids, Session = env
    history(Session, ids)
    c = client.get("/spotlight/challenge").json()
    assert c["game"] == "relic" and c["goal"] == 800 and c["goal_shown"] == "800" and c["done"] is None and not c["goal_is_a_ceiling"]
    values = [100 * n for n in range(1, 13)]
    assert sum(v >= c["goal"] for v in values) / len(values) >= 0.4  # about 40% of past runs reached it
    assert client.get("/spotlight/challenge").json()["goal"] == 800  # the same all day


def test_times_are_a_ceiling(env):
    client, tokens, ids, Session = env
    history(Session, ids, game="speed", key="time", values=[60 + 10 * n for n in range(12)])
    c = client.get("/spotlight/challenge").json()
    assert c["game"] == "speed" and c["goal_is_a_ceiling"] is True
    times = [60 + 10 * n for n in range(12)]
    assert sum(t <= c["goal"] for t in times) / len(times) >= 0.4 and c["goal_shown"].count(":") == 1


def test_no_challenge_without_enough_play(env):
    client, tokens, ids, Session = env
    history(Session, ids, values=[100, 200, 300])
    assert client.get("/spotlight/challenge").json() is None
    assert client.get("/spotlight").json()["challenge"] is None


def test_it_can_only_be_completed_once_a_day(env):
    client, tokens, ids, Session = env
    history(Session, ids)
    client.get("/spotlight/challenge")  # made for the day
    me = ids["boss"]
    play(Session, me, 700)                      # short of 800
    assert client.get("/spotlight/challenge", headers=tokens["boss"]).json()["done"] is False
    play(Session, me, 900, flagged=True)        # held for review: no
    play(Session, me, 5000, game="speed", key="time")  # another game: no
    play(Session, me, 5000, created=ago(days=1))       # yesterday's run: no
    assert client.get("/spotlight/challenge", headers=tokens["boss"]).json()["done"] is False
    play(Session, me, 900)
    play(Session, me, 1100)                     # again: still one
    done = client.get("/spotlight/challenge", headers=tokens["boss"]).json()
    assert done["done"] is True and done["best"] == "1,100"
    with Session() as db:
        assert len(db.scalars(select(ChallengeCompletion)).all()) == 1
        assert db.scalars(select(ChallengeCompletion)).one().value == 900  # the run that did it


def test_a_run_before_looking_at_the_challenge_still_counts(env):
    client, tokens, ids, Session = env
    history(Session, ids)
    play(Session, ids["boss"], 950, created=ago(minutes=1))  # nobody had asked for today's challenge yet: no event can match
    assert client.get("/spotlight/challenge", headers=tokens["boss"]).json()["done"] is True
    assert client.get("/spotlight/challenge", headers=tokens["tunde"]).json()["done"] is False


def test_a_new_day_brings_another_game(env):
    client, tokens, ids, Session = env
    history(Session, ids)
    history(Session, ids, game="speed", key="time", values=[60 + 10 * n for n in range(12)])
    today = service.local_day(service.now())
    with Session() as db:
        first = service.challenge_for(db, today)
        second = service.challenge_for(db, today + timedelta(days=1))
        third = service.challenge_for(db, today + timedelta(days=2))
        assert first.game_name != second.game_name and second.game_name != third.game_name  # never the same game two days running
        assert db.query(DailyChallenge).count() == 3


def test_completing_it_earns_a_badge_once(env, monkeypatch):
    client, tokens, ids, Session = env
    monkeypatch.setattr(progress, "_rules", {"spotlight": list(service.progress._rules["spotlight"])})
    history(Session, ids)
    client.get("/spotlight/challenge")
    with Session() as db:
        assert progress.after_run(db, ids["boss"], "relic", {"score": 500}).unlocked == []
        assert progress.after_run(db, ids["boss"], "relic", {"score": 850}).unlocked == [{"kind": "badge", "key": "daily_challenger"}]
        assert progress.after_run(db, ids["boss"], "relic", {"score": 900}).unlocked == []


def test_game_of_the_week(env):
    client, tokens, ids, Session = env
    boss = tokens["boss"]
    assert client.get("/admin/spotlight", headers=tokens["ada"]).status_code == 404
    assert client.get("/spotlight").json()["game_of_the_week"] is None
    assert client.put("/admin/spotlight/game-of-the-week", json={"game": "relic", "note": "  A great\n one "}, headers=boss).status_code == 204
    pick = client.get("/spotlight").json()["game_of_the_week"]
    assert pick["game"] == "relic" and pick["title"] == "Relic" and pick["note"] == "A great one"
    client.put("/admin/spotlight/game-of-the-week", json={"game": "speed"}, headers=boss)  # replaces this week's
    assert client.get("/spotlight").json()["game_of_the_week"]["game"] == "speed"
    nxt = (service.local_day(service.now()) + timedelta(days=7)).isoformat()
    assert client.put("/admin/spotlight/game-of-the-week", json={"game": "relic", "week": nxt}, headers=boss).status_code == 204
    assert client.get("/spotlight").json()["game_of_the_week"]["game"] == "speed"  # next week's is booked, not shown yet
    last = (service.local_day(service.now()) - timedelta(days=7)).isoformat()
    assert client.put("/admin/spotlight/game-of-the-week", json={"game": "relic", "week": last}, headers=boss).status_code == 400
    assert client.put("/admin/spotlight/game-of-the-week", json={"game": "nope"}, headers=boss).status_code == 404
    assert [p["game"] for p in client.get("/admin/spotlight", headers=boss).json()["picks"]] == ["speed", "relic"]
    with Session() as db:
        db.get(Game, "speed").hidden = "reported"
        db.commit()
        assert service.game_of_the_week(db) is None  # a hidden game is not the pick
    assert client.delete(f"/admin/spotlight/game-of-the-week?week={nxt}", headers=boss).status_code == 204
    assert [p["game"] for p in client.get("/admin/spotlight", headers=boss).json()["picks"]] == ["speed"]
