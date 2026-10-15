"""Games calling the API directly with a game session (POST /games/{name}/session)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.live import create_ticket
from app.main import app
from app.models import Game, User
from app.security import create_token

STYLE = {"body": "female", "primaryColor": "#4b7bec", "secondaryColor": "#2b2f4a", "skinColor": "#8d5524",
         "hairColor": "#17120f", "hair": "braids", "hat": "none", "accessory": "none"}


@pytest.fixture
def env():
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
        users = [User(email=f"{n}@example.com", name=n.title()) for n in ("ada", "tunde")]
        users[0].character = {"name": "Trini", "style": STYLE}
        db.add_all(users)
        db.flush()
        for game in ("relic-hunter", "lanedash"):
            db.add(Game(name=game, owner_id=users[0].id, repo_url=f"https://github.com/ada/{game}"))
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids
    app.dependency_overrides.clear()


def session_for(client, tokens, who, game="relic-hunter"):
    res = client.post(f"/games/{game}/session", headers=tokens[who])
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['session']}"}, res.json()


def test_session_needs_sign_in_and_a_real_game(env):
    client, tokens, _ = env
    assert client.post("/games/relic-hunter/session").status_code == 401
    assert client.post("/games/nope/session", headers=tokens["ada"]).status_code == 404


def test_session_gives_the_player_without_email(env):
    client, tokens, ids = env
    headers, body = session_for(client, tokens, "ada")
    assert body["expires_in"] == 7200
    assert body["player"] == {"id": ids["ada"], "name": "Trini", "style": STYLE}
    player = client.get("/games/relic-hunter/player", headers=headers).json()
    assert player["name"] == "Trini" and "email" not in player
    # someone without a character: their name, no style
    tunde, _ = session_for(client, tokens, "tunde")
    assert client.get("/games/relic-hunter/player", headers=tunde).json() == {"id": ids["tunde"], "name": "Tunde", "style": None}


def test_game_saves_scores_with_its_session(env):
    client, tokens, ids = env
    ada, _ = session_for(client, tokens, "ada")
    tunde, _ = session_for(client, tokens, "tunde")
    assert client.post("/games/relic-hunter/scores", json={"score": 3400}, headers=ada).json() == {"best": 3400, "improved": True, "rank": 1, "stats": {"score": 3400}}
    assert client.post("/games/relic-hunter/scores", json={"score": 2400}, headers=tunde).json()["rank"] == 2
    assert client.post("/games/relic-hunter/scores", json={"score": 100}, headers=ada).json() == {"best": 3400, "improved": False, "rank": 1, "stats": {"score": 3400}}
    board = client.get("/games/relic-hunter/leaderboard").json()
    assert [(e["name"], e["score"]) for e in board] == [("Trini", 3400), ("Tunde", 2400)]


def test_a_session_only_works_for_its_own_game(env):
    client, tokens, _ = env
    ada, _ = session_for(client, tokens, "ada", game="relic-hunter")
    assert client.post("/games/lanedash/scores", json={"score": 10}, headers=ada).status_code == 401
    assert client.get("/games/lanedash/player", headers=ada).status_code == 401


def test_a_session_is_never_a_sign_in(env):
    client, tokens, _ = env
    ada, _ = session_for(client, tokens, "ada")
    assert client.get("/auth/me", headers=ada).status_code == 401
    assert client.get("/games/mine", headers=ada).status_code == 401
    assert client.post("/games/relic-hunter/session", headers=ada).status_code == 401  # can't mint more


def test_live_tickets_cant_save_scores(env):
    client, _, ids = env
    ticket = {"Authorization": f"Bearer {create_ticket(ids['ada'], 'relic-hunter')}"}
    assert client.post("/games/relic-hunter/scores", json={"score": 10}, headers=ticket).status_code == 401


def test_start_ng_pages_still_save_with_the_sign_in(env):
    client, tokens, _ = env
    assert client.post("/games/relic-hunter/scores", json={"score": 50}, headers=tokens["ada"]).status_code == 200


@pytest.mark.parametrize("origin, allowed", [
    ("https://relic-hunter.start.ng", True),
    ("http://relic-hunter.start.localhost:8787", True),
    ("https://evil.com", False),
    ("https://relic-hunter.start.ng.evil.com", False),
])
def test_games_may_call_the_api_from_their_address(env, origin, allowed):
    client, _, _ = env
    res = client.get("/games/relic-hunter/leaderboard", headers={"Origin": origin})
    assert (res.headers.get("access-control-allow-origin") == origin) is allowed
