import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.events import service
from app.features.events.models import Event
from app.main import app
from app.models import User
from app.security import create_token

NOW = datetime.now(timezone.utc)


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

    monkeypatch.setattr(settings, "features", "events")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("boss", "ada")]
        db.add_all(users)
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
    yield TestClient(app), tokens, Session
    app.dependency_overrides.clear()


def body(**over):
    base = {"title": "Tech Ember Fest 2026", "description": "A day outdoors with games, music and food.", "venue": "Crystal Park",
            "starts_at": (NOW + timedelta(days=20)).isoformat(), "ends_at": (NOW + timedelta(days=20, hours=7)).isoformat()}
    return {**base, **over}


def png(width=1200, height=800, color=(200, 80, 20)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def make(client, tokens, **over):
    res = client.post("/admin/events", json=body(**over), headers=tokens["boss"])
    assert res.status_code == 201, res.text
    return res.json()


def add_picture(client, tokens, event_id, data=None):
    return client.post(f"/admin/events/{event_id}/images", content=png() if data is None else data, headers={**tokens["boss"], "Content-Type": "image/png"})


def test_only_admins_with_the_permission_manage_events(env):
    client, tokens, _ = env
    assert client.post("/admin/events", json=body()).status_code == 401
    assert client.post("/admin/events", json=body(), headers=tokens["ada"]).status_code == 404  # not an admin: the admin API looks absent
    assert client.post("/admin/events", json=body(), headers=tokens["boss"]).status_code == 201


def test_an_event_is_checked_before_it_is_saved(env):
    client, tokens, _ = env
    post = lambda **o: client.post("/admin/events", json=body(**o), headers=tokens["boss"])  # noqa: E731
    assert post(ticket_url="javascript:alert(1)").status_code == 422
    assert post(ticket_url="http://insecure.example.com").status_code == 422
    assert post(partners=[{"name": "Nexprove", "url": "ftp://x"}]).status_code == 422
    assert post(ends_at=(NOW + timedelta(days=19)).isoformat()).status_code == 422  # ends before it starts
    assert post(title="x").status_code == 422
    assert post(highlights=[{"title": "t"}] * 7).status_code == 422
    assert post(ticket_url="https://techemberfest.com/tickets").status_code == 201


def test_the_address_is_made_from_the_title_and_never_repeats(env):
    client, tokens, _ = env
    first, second = make(client, tokens), make(client, tokens)
    assert first["slug"] == "tech-ember-fest-2026" and second["slug"] == "tech-ember-fest-2026-2"


def test_a_picture_is_saved_again_as_webp_and_served_for_a_year(env):
    client, tokens, _ = env
    event = make(client, tokens)
    res = add_picture(client, tokens, event["id"], png(3000, 1000))
    assert res.status_code == 201
    pic = res.json()["images"][0]
    got = client.get(pic["path"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/webp"
    assert "immutable" in got.headers["cache-control"]
    assert Image.open(io.BytesIO(got.content)).width == service.MAX_WIDTH  # wide pictures are scaled down


def test_pictures_that_are_not_pictures_or_too_big_are_refused(env):
    client, tokens, _ = env
    event = make(client, tokens)
    assert add_picture(client, tokens, event["id"], b"<script>alert(1)</script>").status_code == 422
    assert add_picture(client, tokens, event["id"], b"").status_code == 400
    assert add_picture(client, tokens, event["id"], b"0" * (service.MAX_UPLOAD_BYTES + 1)).status_code == 413
    for _ in range(service.MAX_IMAGES):
        assert add_picture(client, tokens, event["id"]).status_code == 201
    assert add_picture(client, tokens, event["id"]).status_code == 422  # one too many


def test_the_cover_is_the_first_picture_and_can_be_changed(env):
    client, tokens, _ = env
    event = make(client, tokens)
    add_picture(client, tokens, event["id"])
    second = add_picture(client, tokens, event["id"]).json()["images"][1]["id"]
    res = client.put(f"/admin/events/{event['id']}/images/{second}/cover", headers=tokens["boss"])
    assert res.json()["images"][0]["id"] == second


def test_an_event_needs_a_picture_before_it_can_be_spotlighted(env):
    client, tokens, _ = env
    event = make(client, tokens)
    res = client.put(f"/admin/events/{event['id']}/spotlight", json={"on": True}, headers=tokens["boss"])
    assert res.status_code == 422 and "picture" in res.json()["detail"]
    assert client.get("/events/spotlight").json() is None


def test_spotlighting_one_event_turns_the_other_off(env):
    client, tokens, Session = env
    a, b = make(client, tokens, title="First event"), make(client, tokens, title="Second event")
    for e in (a, b):
        add_picture(client, tokens, e["id"])
    assert client.put(f"/admin/events/{a['id']}/spotlight", json={"on": True}, headers=tokens["boss"]).json()["spotlight"] is True
    assert client.get("/events/spotlight").json()["slug"] == "first-event"
    assert client.put(f"/admin/events/{b['id']}/spotlight", json={"on": True}, headers=tokens["boss"]).json()["spotlight"] is True
    assert client.get("/events/spotlight").json()["slug"] == "second-event"
    with Session() as db:
        assert [e.slug for e in db.scalars(select(Event).where(Event.spotlight.is_(True)))] == ["second-event"]
    client.put(f"/admin/events/{b['id']}/spotlight", json={"on": False}, headers=tokens["boss"])
    assert client.get("/events/spotlight").json() is None  # off: the homepage is back to normal


def test_the_database_itself_refuses_two_spotlighted_events(env):
    _, _, Session = env
    from sqlalchemy.exc import IntegrityError
    with Session() as db:
        for slug in ("one-event", "two-event"):
            db.add(Event(slug=slug, title=slug, description="x" * 10, starts_at=NOW, venue="Somewhere", spotlight=True))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_spotlighted_event_steps_aside_a_day_after_it_ends(env):
    client, tokens, Session = env
    event = make(client, tokens)
    add_picture(client, tokens, event["id"])
    client.put(f"/admin/events/{event['id']}/spotlight", json={"on": True}, headers=tokens["boss"])
    with Session() as db:
        row = db.get(Event, event["id"])
        assert service.spotlighted(db, at=NOW + timedelta(days=21)) is row  # just after it ends
        assert service.spotlighted(db, at=NOW + timedelta(days=22)) is None  # a day later it's gone by itself


def test_an_event_that_is_over_cannot_be_spotlighted(env):
    client, tokens, _ = env
    old = make(client, tokens, starts_at=(NOW - timedelta(days=30)).isoformat(), ends_at=(NOW - timedelta(days=29)).isoformat())
    add_picture(client, tokens, old["id"])
    res = client.put(f"/admin/events/{old['id']}/spotlight", json={"on": True}, headers=tokens["boss"])
    assert res.status_code == 422 and "over" in res.json()["detail"]


def test_removing_the_last_picture_takes_the_event_off_the_homepage(env):
    client, tokens, _ = env
    event = make(client, tokens)
    pic = add_picture(client, tokens, event["id"]).json()["images"][0]["id"]
    client.put(f"/admin/events/{event['id']}/spotlight", json={"on": True}, headers=tokens["boss"])
    res = client.delete(f"/admin/events/{event['id']}/images/{pic}", headers=tokens["boss"])
    assert res.json()["spotlight"] is False and client.get("/events/spotlight").json() is None


def test_the_event_page_and_deleting_an_event(env):
    client, tokens, _ = env
    event = make(client, tokens, tagline="Build. Connect. Play.", highlights=[{"title": "Games", "text": "Physical and stage games"}],
                 partners=[{"name": "Nexprove", "url": "https://www.nexprove.com"}])
    page = client.get(f"/events/{event['slug']}").json()
    assert page["tagline"] == "Build. Connect. Play." and page["highlights"][0]["title"] == "Games" and page["phase"] == "upcoming"
    assert client.delete(f"/admin/events/{event['id']}", headers=tokens["boss"]).status_code == 204
    assert client.get(f"/events/{event['slug']}").status_code == 404
