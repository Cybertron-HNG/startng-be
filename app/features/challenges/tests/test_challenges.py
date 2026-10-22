from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.challenges import service
from app.features.challenges.models import Challenge, ChallengeAttempt
from app.features.email import service as email
from app.features.email.models import Email
from app.features.progress.models import Run
from app.features.share import service as share
from app.main import app
from app.models import Game, User
from app.security import create_token

BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
TIMED = [{"key": "time", "label": "Time", "order": "lowest", "format": "time"}]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


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

    monkeypatch.setattr(settings, "features", "*")
    monkeypatch.setattr(settings, "email_merge_minutes", 0)
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(email, "SessionLocal", Session)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("loki", "ada", "tunde")}
        users["loki"].character = {"name": "Loki", "style": {}}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name="relic-hunter", owner_id=users["ada"].id, repo_url="x", leaderboard=BOARD),
                    Game(name="speed-run", owner_id=users["ada"].id, repo_url="y", leaderboard=TIMED)])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def run(Session, user_id, game, stat, flagged=False, at=None):
    """A run in the log, as the progress feature would write it, then the event it publishes."""
    key = "score" if game == "relic-hunter" else "time"
    with Session() as db:
        r = Run(user_id=user_id, game_name=game, score=stat, stats={key: stat}, flagged=flagged, created_at=at or datetime.now(timezone.utc))
        db.add(r)
        db.commit()
        events.publish("run_recorded", {"user_id": user_id, "game": game, "run_id": r.id})
        return r.id


def test_the_score_comes_from_the_run_log_not_the_request(env):
    client, tokens, ids, Session = env
    assert client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).status_code == 400  # no run yet
    run(Session, ids["loki"], "relic-hunter", 1500)
    best = run(Session, ids["loki"], "relic-hunter", 4200)
    run(Session, ids["loki"], "relic-hunter", 900)
    run(Session, ids["loki"], "relic-hunter", 99999, flagged=True)  # held for review: never the challenge
    made = client.post("/challenges", json={"game": "relic-hunter", "score": 999999}, headers=tokens["loki"])  # a score in the body is ignored
    assert made.status_code == 201 and made.json()["stat"]["value"] == 4200 and made.json()["stat"]["shown"] == "4,200"
    with Session() as db:
        assert db.scalar(select(Challenge)).run_id == best


def test_lower_is_better_games(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "speed-run", 95)
    run(Session, ids["loki"], "speed-run", 65)
    made = client.post("/challenges", json={"game": "speed-run"}, headers=tokens["loki"]).json()
    assert made["stat"]["shown"] == "1:05"


def test_only_your_own_unflagged_run_for_that_game(env):
    client, tokens, ids, Session = env
    theirs = run(Session, ids["ada"], "relic-hunter", 5000)
    flagged = run(Session, ids["loki"], "relic-hunter", 7000, flagged=True)
    other = run(Session, ids["loki"], "speed-run", 70)
    for bad in (theirs, flagged, other):
        assert client.post("/challenges", json={"game": "relic-hunter", "run_id": bad}, headers=tokens["loki"]).status_code == 400
    assert client.post("/challenges", json={"game": "nope"}, headers=tokens["loki"]).status_code == 404
    assert client.post("/challenges", json={"game": "relic-hunter"}).status_code == 401


def test_the_same_run_gives_the_same_link(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    a = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    b = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    assert a == b


def test_anyone_can_see_a_challenge_but_must_sign_in_to_take_it(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    code = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    seen = client.get(f"/challenges/{code}").json()
    assert seen["challenger"] == "Loki" and seen["game_title"] == "Relic Hunter" and seen["attempt"] is None and not seen["mine"]
    assert client.post(f"/challenges/{code}/accept").status_code == 401
    assert client.post(f"/challenges/{code}/accept", headers=tokens["loki"]).status_code == 400  # not your own
    assert client.get(f"/challenges/{code}", headers=tokens["loki"]).json()["mine"] is True
    assert client.get("/challenges/does-not-exist").status_code == 404


def test_a_friend_falls_short_then_beats_it_and_the_challenger_is_told(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    code = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    run(Session, ids["ada"], "relic-hunter", 8000)  # before taking it: doesn't count
    client.post(f"/challenges/{code}/accept", headers=tokens["ada"])
    client.post(f"/challenges/{code}/accept", headers=tokens["ada"])  # twice is fine
    assert client.get(f"/challenges/{code}", headers=tokens["ada"]).json()["attempt"] == {"best": None, "beaten": False, "short_by": None, "attempts": None}

    run(Session, ids["ada"], "relic-hunter", 3900)
    attempt = client.get(f"/challenges/{code}", headers=tokens["ada"]).json()["attempt"]
    assert attempt["best"] == "3,900" and attempt["short_by"] == "300" and not attempt["beaten"]
    run(Session, ids["ada"], "relic-hunter", 3000)  # worse: the best stays
    assert client.get(f"/challenges/{code}", headers=tokens["ada"]).json()["attempt"]["best"] == "3,900"

    with Session() as db:  # Loki wants to hear about it
        email.set_my_topics(db, db.get(User, ids["loki"]), ["records"])
    run(Session, ids["ada"], "relic-hunter", 4300)
    done = client.get(f"/challenges/{code}", headers=tokens["ada"]).json()
    assert done["attempt"]["beaten"] and done["attempt"]["best"] == "4,300" and done["attempt"]["short_by"] is None
    assert done["takers"] == 1 and done["beaten_by"] == 1
    with Session() as db:
        mail = db.scalars(select(Email)).all()
        assert len(mail) == 1 and mail[0].notes[0]["kind"] == "challenge_beaten" and mail[0].notes[0]["data"]["friend"] == "Ada"
    run(Session, ids["ada"], "relic-hunter", 5000)  # beating it again tells nobody twice
    with Session() as db:
        assert len(db.scalars(select(Email)).all()) == 1


def test_a_flagged_run_and_other_games_dont_count(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    code = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    client.post(f"/challenges/{code}/accept", headers=tokens["tunde"])
    run(Session, ids["tunde"], "relic-hunter", 9000, flagged=True)
    run(Session, ids["tunde"], "speed-run", 10)
    assert client.get(f"/challenges/{code}", headers=tokens["tunde"]).json()["attempt"]["best"] is None


def test_challenges_end_and_can_be_deleted(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    code = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    assert [c["code"] for c in client.get("/challenges/mine", headers=tokens["loki"]).json()] == [code]
    assert client.delete(f"/challenges/{code}", headers=tokens["ada"]).status_code == 404  # not theirs
    with Session() as db:
        db.scalar(select(Challenge)).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.get(f"/challenges/{code}").status_code == 404
    assert client.get("/challenges/mine", headers=tokens["loki"]).json() == []
    code2 = None
    run(Session, ids["loki"], "relic-hunter", 5000)
    code2 = client.post("/challenges", json={"game": "relic-hunter", "run_id": None}, headers=tokens["loki"]).json()["code"]
    assert client.delete(f"/challenges/{code2}", headers=tokens["loki"]).status_code == 204
    assert client.get(f"/challenges/{code2}").status_code == 404


def test_a_hidden_game_closes_its_challenges(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    code = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    with Session() as db:
        db.get(Game, "relic-hunter").hidden = "reported"
        db.commit()
    assert client.get(f"/challenges/{code}").status_code == 404


def test_not_too_many_at_once(env, monkeypatch):
    client, tokens, ids, Session = env
    monkeypatch.setattr(service, "MAX_PER_HOUR", 2)
    for score in (100, 200, 300):
        run(Session, ids["loki"], "relic-hunter", score)
    codes = [client.post("/challenges", json={"game": "relic-hunter", "run_id": rid}, headers=tokens["loki"]).status_code
             for rid in (r for r in range(1, 4))]
    assert codes == [201, 201, 429]


def test_the_share_card_says_what_the_challenge_is(env):
    client, tokens, ids, Session = env
    run(Session, ids["loki"], "relic-hunter", 4200)
    code = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()["code"]
    with Session() as db:
        made = share.make(db, "challenge", code)
        assert made.url.endswith(f"/s/challenge/{code}") and "4,200" in made.text and "Relic Hunter" in made.text
        card = share.describe(db, "challenge", code)
        assert card.target == f"/c/{code}" and card.title == "Loki scored 4,200 on Relic Hunter"
    assert client.get(f"/share/challenge/{code}/card.png").status_code == 200
