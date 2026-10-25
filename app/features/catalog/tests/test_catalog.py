import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import manifest
from app.config import settings
from app.db import Base, get_db
from app.features.catalog import service
from app.features.catalog.models import GameCatalog, GameRating
from app.features.progress.models import GameOpen
from app.main import app
from app.models import Game, User
from app.security import create_token

PLAYERS = ("owner", "ada", "tunde", "chioma", "musa", "kofi", "zainab")


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

    monkeypatch.setattr(settings, "features", "catalog,progress")
    monkeypatch.setattr(settings, "super_admin_emails", "owner@example.com")
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in PLAYERS}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name="lane-dash", owner_id=users["owner"].id, repo_url="x"), Game(name="relic", owner_id=users["owner"].id, repo_url="y")])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def opened(Session, user_id, game="lane-dash", seconds_ago=120):
    when = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    with Session() as db:
        db.add(GameOpen(user_id=user_id, game_name=game, first_at=when, last_at=when, count=1))
        db.commit()


def test_a_player_who_never_opened_a_game_cant_rate_it(env):
    client, tokens, ids, Session = env
    r = client.put("/games/lane-dash/rating", json={"stars": 5}, headers=tokens["ada"])
    assert r.status_code == 403 and "Play the game" in r.json()["detail"]
    assert client.get("/games/lane-dash/rating", headers=tokens["ada"]).json()["can_rate"] is False
    opened(Session, ids["ada"], seconds_ago=1)  # opened a moment ago: that is enough, there is no waiting time
    assert client.get("/games/lane-dash/rating", headers=tokens["ada"]).json()["can_rate"] is True
    assert client.put("/games/lane-dash/rating", json={"stars": 5}, headers=tokens["ada"]).status_code == 200


def test_a_saved_score_counts_as_playing_even_when_the_open_was_never_recorded(env):
    from app.features.progress.models import Run

    client, tokens, ids, Session = env
    assert client.get("/games/lane-dash/rating", headers=tokens["ada"]).json()["can_rate"] is False
    with Session() as db:  # no GameOpen row: they reached the game by its own address
        db.add(Run(user_id=ids["ada"], game_name="lane-dash", score=40, stats={"score": 40}))
        db.commit()
    assert client.get("/games/lane-dash/rating", headers=tokens["ada"]).json()["can_rate"] is True
    assert client.get("/games/relic/rating", headers=tokens["ada"]).json()["can_rate"] is False  # only that game


def test_owners_cant_rate_their_own_game_and_signed_out_cant_rate(env):
    client, tokens, ids, Session = env
    opened(Session, ids["owner"])
    assert client.put("/games/lane-dash/rating", json={"stars": 5}, headers=tokens["owner"]).status_code == 403
    assert client.put("/games/lane-dash/rating", json={"stars": 5}).status_code == 401
    assert client.get("/games/lane-dash/rating").json()["why_not"] == "Sign in to rate games"


def test_rate_change_and_remove(env):
    client, tokens, ids, Session = env
    opened(Session, ids["ada"])
    assert client.put("/games/lane-dash/rating", json={"stars": 9}, headers=tokens["ada"]).status_code == 422
    one = client.put("/games/lane-dash/rating", json={"stars": 4}, headers=tokens["ada"]).json()
    assert one["mine"] == 4 and one["rating_count"] == 1 and one["rating_avg"] is None  # stars show from 5 ratings
    assert client.put("/games/lane-dash/rating", json={"stars": 2}, headers=tokens["ada"]).json()["mine"] == 2  # one per player
    with Session() as db:
        assert db.query(GameRating).count() == 1
    gone = client.delete("/games/lane-dash/rating", headers=tokens["ada"]).json()
    assert gone["mine"] is None and gone["rating_count"] == 0


def test_stars_show_after_five_ratings_and_a_few_votes_dont_outrank_many(env):
    client, tokens, ids, Session = env
    for who, stars in zip(PLAYERS[1:], (5, 4, 5, 4, 5, 4)):
        opened(Session, ids[who])
        client.put("/games/lane-dash/rating", json={"stars": stars}, headers=tokens[who])
    opened(Session, ids["ada"], "relic")
    client.put("/games/relic/rating", json={"stars": 5}, headers=tokens["ada"])  # a single 5-star vote
    games = {g["game"]: g for g in client.get("/catalog").json()["games"]}
    assert games["lane-dash"]["rating_count"] == 6 and games["lane-dash"]["rating_avg"] == 4.5
    assert games["relic"]["rating_avg"] is None and games["relic"]["rating_count"] == 1
    assert games["lane-dash"]["score"] > games["relic"]["score"]  # 6 votes at 4.5 beat one vote at 5.0


def test_category_from_the_file_and_from_the_owner(env):
    client, tokens, ids, Session = env
    assert client.get("/catalog").json()["categories"][:3] == ["Action", "Racing", "Puzzle"]
    # The owner picks one in Your games ...
    set_ = client.put("/games/lane-dash/category", json={"category": "racing"}, headers=tokens["owner"]).json()
    assert set_["category"] == "Racing"
    assert client.put("/games/lane-dash/category", json={"category": "Racing"}, headers=tokens["ada"]).status_code == 403
    assert client.put("/games/lane-dash/category", json={"category": "Nonsense"}, headers=tokens["owner"]).status_code == 400
    # ... and startng.json wins when the game is published.
    values = manifest.parse_sections(json.dumps({"category": "puzzle"}).encode())
    assert values == {"category": "Puzzle"}
    with Session() as db:
        manifest.apply_sections(db, db.get(Game, "lane-dash"), values)
        db.commit()
        row = db.get(GameCatalog, "lane-dash")
        assert (row.category, row.category_source) == ("Puzzle", "file")
    with pytest.raises(manifest.SchemaError, match="category must be one of"):
        manifest.parse_sections(b'{"category": "Spaceships"}')
    assert client.put("/games/lane-dash/category", json={"category": None}, headers=tokens["owner"]).json()["category"] is None


def test_admins_can_remove_an_abusive_rating(env):
    client, tokens, ids, Session = env
    opened(Session, ids["ada"])
    client.put("/games/lane-dash/rating", json={"stars": 1}, headers=tokens["ada"])
    assert client.get("/admin/ratings", headers=tokens["ada"]).status_code == 404  # not an admin
    rows = client.get("/admin/ratings?game=lane-dash", headers=tokens["owner"]).json()
    assert [(r["player"], r["stars"]) for r in rows] == [("Ada", 1)]
    assert client.delete(f"/admin/ratings/{rows[0]['id']}", headers=tokens["owner"]).status_code == 204
    assert client.get("/games/lane-dash/rating").json()["rating_count"] == 0
    assert client.delete("/admin/ratings/999", headers=tokens["owner"]).status_code == 404


def test_hidden_games_are_left_out(env):
    client, tokens, ids, Session = env
    client.put("/games/relic/category", json={"category": "Puzzle"}, headers=tokens["owner"])
    with Session() as db:
        db.get(Game, "relic").hidden = "reported"
        db.commit()
    assert [g["game"] for g in client.get("/catalog").json()["games"]] == []
    assert client.get("/games/relic/rating").status_code == 404
