from collections import deque, defaultdict
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Game, User
from app.security import create_token


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
        user = User(email="ada@example.com", name="Ada Lovelace")
        db.add(user)
        db.flush()
        # Game with explicit description
        db.add(Game(name="coin-rush", owner_id=user.id, repo_url="https://github.com/ada/coin-rush", description="Collect as many coins as you can!"))
        # Game without description (like lagos-run)
        db.add(Game(name="lagos-run", owner_id=user.id, repo_url="https://github.com/ada/lagos-run"))
        # Hangout
        db.add(Game(name="hangout", owner_id=user.id, repo_url="https://github.com/ada/hangout"))
        db.commit()
        token = {"Authorization": f"Bearer {create_token(user.id)}"}
    yield TestClient(app), token
    app.dependency_overrides.clear()


def test_games_api_supplies_description_for_all_games(env):
    client, _ = env
    games = client.get("/games").json()
    assert len(games) == 3

    by_name = {g["name"]: g for g in games}

    # Explicit description is preserved
    assert by_name["coin-rush"]["description"] == "Collect as many coins as you can!"

    # Empty description falls back to game-page one-liner
    assert by_name["lagos-run"]["description"] == "Play Lagos Run free in your browser on start.ng, a game by Ada Lovelace."

    # Hangout has the community park fallback description
    assert "multiplayer park" in by_name["hangout"]["description"]

    # Every game in the feed has a valid, non-empty description
    for g in games:
        assert g["description"] and len(g["description"]) > 10
