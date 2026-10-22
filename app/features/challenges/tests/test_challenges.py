from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.challenges import service
from app.features.challenges.models import Challenge
from app.features.integrity import service as integrity
from app.features.progress.models import Run
from app.features.share_cards import service as share
from app.main import app
from app.models import Game, User
from app.security import create_token

HIGH = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
LOW = [{"key": "time", "label": "Time", "order": "lowest", "format": "time"}]
PLAYERS = ("loki", "ada", "tunde")


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
    monkeypatch.setattr(service, "SessionLocal", Session)  # run_recorded opens its own session
    emails = []
    monkeypatch.setattr(service.email, "notify", lambda user_id, kind, data, db=None: emails.append((user_id, kind, data)) or True)
    integrity.reset()
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in PLAYERS]
        db.add_all(users)
        db.flush()
        db.add_all([Game(name="relic-hunter", owner_id=users[0].id, repo_url="https://github.com/x/a", leaderboard=HIGH),
                    Game(name="lane-dash", owner_id=users[0].id, repo_url="https://github.com/x/b", leaderboard=LOW)])
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session, emails
    app.dependency_overrides.clear()
    integrity.reset()


def play(client, tokens, who, game, value):
    stats = {"time": value} if game == "lane-dash" else {"score": value}
    res = client.post(f"/games/{game}/scores", json={"stats": stats}, headers=tokens[who])
    assert res.status_code == 200, res.text


def last_run(Session, user_id):
    with Session() as db:
        return db.scalar(select(Run).where(Run.user_id == user_id).order_by(Run.id.desc())).id


def challenge(client, tokens, Session, ids, who="loki", game="relic-hunter", value=4200):
    play(client, tokens, who, game, value)
    res = client.post("/challenges", json={"run_id": last_run(Session, ids[who])}, headers=tokens[who])
    assert res.status_code == 201, res.text
    return res.json()


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, *_ = env
    monkeypatch.setattr(settings, "features", "progress,integrity")
    assert client.post("/challenges", json={"run_id": 1}, headers=tokens["loki"]).status_code == 404


def test_a_challenge_from_your_run(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids)
    assert (c["score"], c["score_text"], c["mine"], c["challenger"]["name"]) == (4200, "4,200", True, "Loki")
    assert c["url"] == f"https://start.ng/c/{c['code']}" and len(c["code"]) >= 11  # not guessable
    assert "email" not in str(c)


def test_the_score_comes_from_the_run_not_the_request(env):
    client, tokens, ids, Session, _ = env
    play(client, tokens, "loki", "relic-hunter", 100)
    res = client.post("/challenges", json={"run_id": last_run(Session, ids["loki"]), "score": 999999}, headers=tokens["loki"])
    assert res.json()["score"] == 100


def test_only_your_own_unflagged_runs(env):
    client, tokens, ids, Session, _ = env
    play(client, tokens, "ada", "relic-hunter", 300)
    ada_run = last_run(Session, ids["ada"])
    assert client.post("/challenges", json={"run_id": ada_run}, headers=tokens["loki"]).status_code == 404
    assert client.post("/challenges", json={"run_id": 999}, headers=tokens["loki"]).status_code == 404
    with Session() as db:
        db.get(Run, ada_run).flagged = True
        db.commit()
    assert client.post("/challenges", json={"run_id": ada_run}, headers=tokens["ada"]).status_code == 409


def test_same_run_same_link(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids)
    again = client.post("/challenges", json={"run_id": last_run(Session, ids["loki"])}, headers=tokens["loki"])
    assert again.json()["code"] == c["code"]


def test_anyone_can_see_it_signing_in_to_take_it_on(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids)
    seen = client.get(f"/challenges/{c['code']}")
    assert seen.status_code == 200 and seen.json()["mine"] is False and seen.json()["attempts"] == []
    assert client.post(f"/challenges/{c['code']}/accept").status_code == 401
    assert client.post(f"/challenges/{c['code']}/accept", headers=tokens["loki"]).status_code == 409  # your own


def test_beating_it(env):
    client, tokens, ids, Session, emails = env
    c = challenge(client, tokens, Session, ids, value=400)
    seen = []
    handler = events.subscribe("challenge_beaten")(lambda p: seen.append(p))
    try:
        play(client, tokens, "ada", "relic-hunter", 350)  # before taking it on: doesn't count
        ada = client.post(f"/challenges/{c['code']}/accept", headers=tokens["ada"]).json()["me"]
        assert ada == {"player": ada["player"], "best": None, "best_text": None, "beaten": False, "short_by": None}
        play(client, tokens, "ada", "relic-hunter", 300)
        me = client.get(f"/challenges/{c['code']}", headers=tokens["ada"]).json()["me"]
        assert (me["best"], me["short_by"], me["beaten"]) == (300, 100, False)  # "100 short"
        assert seen == [] and emails == []
        play(client, tokens, "ada", "relic-hunter", 450)
        play(client, tokens, "ada", "relic-hunter", 500)  # beaten again: told only once
        me = client.get(f"/challenges/{c['code']}", headers=tokens["ada"]).json()["me"]
        assert (me["best"], me["beaten"], me["short_by"]) == (500, True, None)
    finally:
        events.unsubscribe("challenge_beaten", handler)
    assert seen == [{"challenge_id": seen[0]["challenge_id"], "challenger_id": ids["loki"], "user_id": ids["ada"]}]
    assert [(e[0], e[1], e[2]["theirs"], e[2]["yours"]) for e in emails] == [(ids["loki"], "challenge_beaten", "450", "400")]
    board = client.get(f"/challenges/{c['code']}", headers=tokens["loki"]).json()
    assert [(a["player"]["name"], a["best"], a["beaten"]) for a in board["attempts"]] == [("Ada", 500, True)]


def test_lower_is_better(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids, game="lane-dash", value=185)
    assert c["score_text"] == "3:05"
    client.post(f"/challenges/{c['code']}/accept", headers=tokens["ada"])
    play(client, tokens, "ada", "lane-dash", 200)
    assert client.get(f"/challenges/{c['code']}", headers=tokens["ada"]).json()["me"]["short_by"] == 15
    play(client, tokens, "ada", "lane-dash", 180)
    assert client.get(f"/challenges/{c['code']}", headers=tokens["ada"]).json()["me"]["beaten"] is True


def test_other_games_and_flagged_runs_dont_count(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids, value=400)
    client.post(f"/challenges/{c['code']}/accept", headers=tokens["ada"])
    play(client, tokens, "ada", "lane-dash", 100)
    with Session() as db:  # a run held for review (score integrity never publishes run_recorded for it)
        run = Run(user_id=ids["ada"], game_name="relic-hunter", score=9000, stats={"score": 9000}, flagged=True,
                  created_at=service.now() + timedelta(seconds=1))
        db.add(run)
        db.commit()
        assert service.count_run(db, run) == []
    assert client.get(f"/challenges/{c['code']}", headers=tokens["ada"]).json()["me"]["best"] is None


def test_expired_and_deleted(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids)
    assert client.delete(f"/challenges/{c['code']}", headers=tokens["ada"]).status_code == 403
    with Session() as db:
        db.scalar(select(Challenge)).expires_at = service.now() - timedelta(seconds=1)
        db.commit()
    assert client.get(f"/challenges/{c['code']}").status_code == 404
    d = challenge(client, tokens, Session, ids, value=4300)
    assert client.delete(f"/challenges/{d['code']}", headers=tokens["loki"]).status_code == 204
    assert client.get(f"/challenges/{d['code']}").status_code == 404


def test_its_share_card(env):
    client, tokens, ids, Session, _ = env
    c = challenge(client, tokens, Session, ids)
    with Session() as db:
        card = share.describe(db, "challenge", c["code"])
        assert share.describe(db, "challenge", "nope-nope-nope") is None
    assert (card.name, card.title, card.game, card.path) == ("Loki", "scored 4,200", "Relic Hunter", f"/c/{c['code']}")
    assert card.cta == "Can you beat 4,200?"
    assert client.get(f"/share/challenge/{c['code']}/card.jpg").status_code == 200


def test_the_email(env):
    note = service._render_beaten({"friend": "Ada <b>", "game": "relic-hunter", "code": "abc", "theirs": "450", "yours": "400"})
    assert note.subject == "Ada <b> beat your challenge on Relic Hunter"
    assert "Ada &lt;b&gt;" in note.html and "https://start.ng/c/abc" in note.text


def test_from_your_best_run_on_a_game(env):
    client, tokens, ids, Session, _ = env
    for v in (300, 500, 400):
        play(client, tokens, "loki", "relic-hunter", v)
    for v in (200, 170, 190):
        play(client, tokens, "loki", "lane-dash", v)
    best = client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["loki"]).json()
    fastest = client.post("/challenges", json={"game": "lane-dash"}, headers=tokens["loki"]).json()
    assert (best["score"], fastest["score"]) == (500, 170)  # lower is better on a time
    assert client.post("/challenges", json={"game": "relic-hunter"}, headers=tokens["ada"]).status_code == 404  # never played
    assert client.post("/challenges", json={}, headers=tokens["loki"]).status_code == 422
    assert client.post("/challenges", json={"game": "relic-hunter", "run_id": 1}, headers=tokens["loki"]).status_code == 422
