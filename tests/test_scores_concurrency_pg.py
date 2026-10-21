"""Needs a real Postgres (SQLite can't show lost updates). Point TEST_DATABASE_URL at a THROWAWAY database:

    createdb startng_conc && TEST_DATABASE_URL=postgresql+psycopg://localhost/startng_conc pytest tests/test_scores_concurrency_pg.py

It creates the tables in that database and drops them again. Skipped when the variable isn't set (CI, normal runs)."""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Game, Score, User
from app.routers.games import ScoreIn, submit_score

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs a real Postgres: set TEST_DATABASE_URL to a throwaway database")


@pytest.fixture
def sessions():
    engine = create_engine(URL, pool_size=16)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as db:
        user = User(email="racer@example.com", name="Racer")
        db.add(user)
        db.flush()
        db.add(Game(name="lanedash", owner_id=user.id, repo_url="https://github.com/a/lanedash"))
        db.add(Score(user_id=user.id, game_name="lanedash", best=10, stats={"score": 10}))
        db.commit()
        uid = user.id
    yield Session, uid
    engine.dispose()
    Base.metadata.drop_all(create_engine(URL))


def test_a_lower_score_sent_at_the_same_time_never_replaces_a_higher_best(sessions):
    Session, uid = sessions
    scores = list(range(20, 140, 10))  # 20 ... 130, all better than the saved 10
    for trial in range(6):
        with Session() as db:
            row = db.scalar(select(Score))
            row.best, row.stats = 10, {"score": 10}
            db.commit()
        start = threading.Barrier(len(scores))

        def one(n):
            with Session() as db:
                start.wait()
                submit_score("lanedash", ScoreIn(score=n), db.get(User, uid), db)

        with ThreadPoolExecutor(len(scores)) as pool:
            list(pool.map(one, scores))
        with Session() as db:
            assert db.scalar(select(Score)).best == max(scores), f"trial {trial}: a lower score overwrote the best"
