import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Game, User
from app.security import create_token

# alembic/versions isn't a package, so load the migration from its file.
_path = Path(__file__).parent.parent / "alembic" / "versions" / "0007_seed_hangout.py"
_spec = importlib.util.spec_from_file_location("seed_hangout", _path)
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)


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

    def migrate(step):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            step()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), Session, migrate
    app.dependency_overrides.clear()


def test_hangout_belongs_to_a_start_ng_account_nobody_can_sign_in_to(env):
    _, Session, migrate = env
    migrate(seed.upgrade)
    with Session() as db:
        game = db.get(Game, "hangout")
        assert game.owner.name == "start.ng"
        assert game.owner.password_hash is None and game.owner.google_sub is None
        assert game.repo_url == seed.REPO_URL


def test_it_shows_as_by_start_ng_and_the_live_room_ticket_works(env):
    client, Session, migrate = env
    migrate(seed.upgrade)
    with Session() as db:
        player = User(email="ada@example.com", name="Ada")
        db.add(player)
        db.commit()
        auth = {"Authorization": f"Bearer {create_token(player.id)}"}
    assert [g["by"] for g in client.get("/games").json() if g["name"] == "hangout"] == ["start.ng"]
    assert client.post("/games/hangout/ticket", headers=auth).status_code == 200


def test_running_it_twice_changes_nothing(env):
    _, Session, migrate = env
    migrate(seed.upgrade)
    migrate(seed.upgrade)
    with Session() as db:
        assert len(db.scalars(select(Game)).all()) == 1
        assert len(db.scalars(select(User)).all()) == 1


def test_a_hangout_someone_already_published_is_left_alone(env):
    _, Session, migrate = env
    with Session() as db:
        ada = User(email="ada@example.com", name="Ada")
        db.add(ada)
        db.flush()
        db.add(Game(name="hangout", owner_id=ada.id, repo_url="https://github.com/ada/hangout"))
        db.commit()
    migrate(seed.upgrade)
    with Session() as db:
        assert db.get(Game, "hangout").owner.email == "ada@example.com"
        assert len(db.scalars(select(User)).all()) == 1
    migrate(seed.downgrade)  # nothing was seeded, so nothing is removed
    with Session() as db:
        assert db.get(Game, "hangout") is not None


def test_downgrade_removes_what_was_seeded(env):
    _, Session, migrate = env
    migrate(seed.upgrade)
    migrate(seed.downgrade)
    with Session() as db:
        assert db.scalars(select(Game)).all() == []
        assert db.scalars(select(User)).all() == []
