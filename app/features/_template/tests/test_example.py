"""How to test a feature: its own app with just its router, an in-memory database, and the flag switched on."""
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import flags
from app.config import settings
from app.db import Base, get_db
from app.features import load
from app.models import User
from app.security import create_token


def test_adding_a_note(monkeypatch):
    [feature] = load(["_template"])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    app = FastAPI()
    app.include_router(feature.router, dependencies=[Depends(flags.require(feature.NAME))])
    app.dependency_overrides[get_db] = lambda: (yield Session()) or None
    with Session() as db:
        db.add(User(email="ada@example.com", name="Ada"))
        db.commit()
    headers = {"Authorization": f"Bearer {create_token(1)}"}
    client = TestClient(app)

    monkeypatch.setattr(settings, "features", "")
    assert client.post("/example/notes", json={"text": "hi"}, headers=headers).status_code == 404  # ships dark
    monkeypatch.setattr(settings, "features", feature.NAME)
    res = client.post("/example/notes", json={"text": "hi"}, headers=headers)
    assert res.status_code == 201 and res.json()["text"] == "hi"
