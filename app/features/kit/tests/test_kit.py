from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.kit import service
from app.features.kit.models import PlaySession
from app.main import app
from app.models import Game, User
from app.security import create_token

SID = "0f8c5b1e-2d3a-4c6b-9e7f-1a2b3c4d5e6f"


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
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        ada = User(email="ada@example.com", name="Ada")
        db.add(ada)
        db.flush()
        db.add_all([Game(name="tap-rush", owner_id=ada.id, repo_url="x"), Game(name="other", owner_id=ada.id, repo_url="y")])
        db.commit()
        token = {"Authorization": f"Bearer {create_token(ada.id)}"}
        ada_id = ada.id
    yield TestClient(app), token, ada_id, Session
    app.dependency_overrides.clear()


def ping(client, event, game="tap-rush", sid=SID, headers=None):
    return client.post(f"/kit/games/{game}/sessions", json={"session_id": sid, "event": event, "kit": "0.1.0"}, headers=headers or {})


def rows(Session):
    with Session() as db:
        return db.scalars(select(PlaySession)).all()


def age(Session, **shift):
    """Moves the session back in time, as if its pings came that long ago."""
    with Session() as db:
        row = db.scalar(select(PlaySession))
        row.started_at -= timedelta(**shift)
        row.last_seen_at -= timedelta(**shift)
        db.commit()


def test_off_without_its_flag(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(settings, "features", "")
    assert ping(client, "start").status_code == 404


def test_a_guest_session_starts_beats_and_ends(env):
    client, _, _, Session = env
    assert ping(client, "start").status_code == 204
    age(Session, seconds=90)
    assert ping(client, "heartbeat").status_code == 204
    assert ping(client, "end").status_code == 204
    [row] = rows(Session)
    assert (row.game_name, row.user_id, row.kit_version) == ("tap-rush", None, "0.1.0")
    assert row.ended_at is not None
    assert 85 <= service.length_seconds(row) <= 95


def test_a_signed_in_player_is_saved(env):
    client, token, ada_id, Session = env
    ping(client, "start", headers=token)
    assert rows(Session)[0].user_id == ada_id


def test_a_heartbeat_without_a_start_starts_the_session(env):
    client, _, _, Session = env
    ping(client, "heartbeat")
    assert len(rows(Session)) == 1


def test_an_end_for_an_unknown_session_is_ignored(env):
    client, _, _, Session = env
    assert ping(client, "end").status_code == 204
    assert rows(Session) == []


def test_quick_heartbeats_are_not_written(env):
    client, _, _, Session = env
    ping(client, "start")
    first = rows(Session)[0].last_seen_at
    ping(client, "heartbeat")  # under MIN_WRITE_SECONDS later
    assert rows(Session)[0].last_seen_at == first


def test_a_session_id_stays_with_its_game(env):
    client, _, _, Session = env
    ping(client, "start", game="tap-rush")
    age(Session, seconds=60)
    ping(client, "heartbeat", game="other")
    [row] = rows(Session)
    assert row.game_name == "tap-rush"
    assert service.length_seconds(row) == 0  # the other game's ping didn't touch it


@pytest.mark.parametrize("body", [
    {"session_id": "short", "event": "start"},  # too short
    {"session_id": "x" * 65, "event": "start"},  # too long
    {"session_id": "bad id with spaces!!", "event": "start"},
    {"session_id": SID, "event": "pause"},
])
def test_bad_pings_are_refused(env, body):
    client, *_ = env
    assert client.post("/kit/games/tap-rush/sessions", json=body).status_code == 422


def test_unknown_game(env):
    client, *_ = env
    assert ping(client, "start", game="nope").status_code == 404


def test_stats_average_and_long_sessions_are_capped(env):
    client, _, _, Session = env
    ping(client, "start", sid="a" * 20)
    age(Session, seconds=120)
    ping(client, "heartbeat", sid="a" * 20)
    with Session() as db:
        db.add(PlaySession(session_id="b" * 20, game_name="tap-rush", started_at=datetime.now(timezone.utc) - timedelta(hours=10),
                           last_seen_at=datetime.now(timezone.utc)))  # a tab left open for 10 hours
        db.commit()
        result = service.stats(db, "tap-rush")
    assert result.sessions == 2
    assert result.average_seconds == round((120 + service.MAX_SESSION_HOURS * 3600) / 2)


def test_sessions_older_than_30_days_are_deleted_when_a_new_one_starts(env):
    client, _, _, Session = env
    ping(client, "start", sid="o" * 20)
    age(Session, days=31)
    ping(client, "start", sid="n" * 20)
    assert [r.session_id for r in rows(Session)] == ["n" * 20]

def test_clip_shared_ping(env):
    client, _, _, Session = env
    assert ping(client, "start").status_code == 204
    assert ping(client, "clip_shared").status_code == 204
    with Session() as db:
        row = db.scalar(select(PlaySession))
        assert row.started_at is not None

