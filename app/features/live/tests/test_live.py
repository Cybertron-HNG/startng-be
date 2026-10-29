from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.kit.models import PlaySession
from app.features.live import service
from app.features.profiles import service as profiles
from app.main import app
from app.models import Game, Tournament, TournamentGame, User

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "features", "live,kit,profiles")
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
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "now", lambda: NOW)
    with Session() as db:
        ada, bo = User(email="ada@example.com", name="Ada"), User(email="bo@example.com", name="Bo")
        db.add_all([ada, bo])
        db.flush()
        for n in ("lane-dash", "danfo-make-way", "quiet-one", "hangout"):
            db.add(Game(name=n, owner_id=ada.id, repo_url=f"https://github.com/x/{n}"))
        db.commit()
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def sessions(db, game, n, seen_ago=timedelta(seconds=20), ended=False):
    for i in range(n):
        db.add(PlaySession(session_id=f"{game}-{seen_ago.seconds}-{i}", game_name=game, started_at=NOW - timedelta(minutes=5),
                           last_seen_at=NOW - seen_ago, ended_at=NOW if ended else None))


def test_playing_now_counts_open_sessions_and_hides_a_lonely_one(env):
    client, Session = env
    with Session() as db:
        sessions(db, "lane-dash", 3)
        sessions(db, "danfo-make-way", 1)  # one player: not shown
        sessions(db, "quiet-one", 4, seen_ago=timedelta(minutes=5))  # left a while ago
        sessions(db, "quiet-one", 2, ended=True)  # closed the tab
        sessions(db, "hangout", 5)  # start.ng's own space, not a game
        db.commit()
        assert service.snapshot(db, NOW)["playing"] == {"lane-dash": 3}


def test_a_new_record_is_a_moment_and_private_players_are_not_named(env):
    client, Session = env
    events.publish("record_beaten", {"game": "danfo-make-way", "user_id": 1, "previous_user_id": 2})
    with Session() as db:
        assert [m["text"] for m in service.snapshot(db, NOW)["moments"]] == ["Ada took #1 on Danfo Make Way"]
        profiles.update_settings(db, 1, public=False)
        db.commit()
        assert [m["text"] for m in service.snapshot(db, NOW)["moments"]] == ["Someone took #1 on Danfo Make Way"]
        service.record_moment(db, "record", "lane-dash", 2, at=NOW - timedelta(days=2))  # too old to be live
        db.commit()
        assert len(service.snapshot(db, NOW)["moments"]) == 1


def test_players_on_todays_tournament_games(env):
    client, Session = env
    with Session() as db:
        db.add(Tournament(starts_at=NOW - timedelta(hours=2), ends_at=NOW + timedelta(hours=6),
                          games=[TournamentGame(game_name="lane-dash", position=0), TournamentGame(game_name="danfo-make-way", position=1)]))
        sessions(db, "lane-dash", 1)
        sessions(db, "danfo-make-way", 1)
        db.commit()
        snap = service.snapshot(db, NOW)
        assert snap["tournament_playing"] == 2 and snap["playing"] == {}  # each game alone is too few, together they count


def test_the_route_ships_dark_and_caches(env, monkeypatch):
    client, Session = env
    res = client.get("/live")
    assert res.status_code == 200 and res.json() == {"playing": {}, "moments": [], "tournament_playing": 0}
    assert res.headers["cache-control"] == "public, max-age=15"
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/live").status_code == 404
