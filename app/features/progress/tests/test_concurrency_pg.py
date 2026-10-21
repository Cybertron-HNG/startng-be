"""Needs a real Postgres (SQLite can't show lost updates). Point TEST_DATABASE_URL at a THROWAWAY database:

    createdb startng_conc && TEST_DATABASE_URL=postgresql+psycopg://localhost/startng_conc pytest app/features/progress/tests/test_concurrency_pg.py

It creates the tables in that database and drops them again. Skipped when the variable isn't set (CI, normal runs)."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.features.progress import service as progress
from app.features.progress.models import ActivityDay, Run
from app.models import Game, User

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs a real Postgres: set TEST_DATABASE_URL to a throwaway database")


@pytest.fixture
def sessions(monkeypatch):
    engine = create_engine(URL, pool_size=12)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as db:
        user = User(email="racer@example.com", name="Racer")
        db.add(user)
        db.flush()
        db.add(Game(name="lanedash", owner_id=user.id, repo_url="https://github.com/a/lanedash"))
        db.commit()
        uid = user.id
    monkeypatch.setattr(progress, "now", lambda: datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc))
    yield Session, uid
    engine.dispose()
    Base.metadata.drop_all(create_engine(URL))


def test_simultaneous_runs_in_a_new_players_first_day_are_all_counted_once(sessions):
    Session, uid = sessions
    runs = 8

    def one(n):
        with Session() as db:
            progress.after_run(db, uid, "lanedash", {"score": n})

    with ThreadPoolExecutor(runs) as pool:
        list(pool.map(one, range(runs)))
    with Session() as db:
        day = db.scalars(select(ActivityDay)).one()
        assert db.scalars(select(Run)).all().__len__() == runs
        assert day.runs == runs  # no run lost
        assert day.xp == progress.FIRST_RUN_XP + progress.RUN_XP * runs  # the first-run bonus given once, nothing lost
