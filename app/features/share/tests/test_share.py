import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.share import render, service
from app.main import app
from app.models import Game, Tournament, TournamentGame, TournamentPoints, User
from app.routers import pages

SHELL = '<html><head><!--seo--><title>x</title><!--/seo--></head><body><div id="root"></div></body></html>'
state = {"title": "Loki scored 4,200", "target": "/games/relic-hunter"}


def describe(db, item):
    if item == "missing":
        return None
    return service.Card(kicker="Challenge", title=state["title"], subtitle="Can you beat it?", numbers=[("Score", "4,200")],
                        target=state["target"], text="Beat me!")


service.register("example", describe)


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

    monkeypatch.setattr(settings, "features", "share")
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    monkeypatch.setattr(render, "_fetch", lambda url: None)
    render._pictures.clear()
    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), Session
    app.dependency_overrides.clear()
    state.update(title="Loki scored 4,200", target="/games/relic-hunter")


def test_make_gives_a_link_text_and_picture(env):
    client, Session = env
    with Session() as db:
        made = service.make(db, "example", "abc")
        assert made.url.endswith("/s/example/abc") and made.text == "Beat me!"
        assert made.image_url.endswith("/share/example/abc/card.png?size=wide")
        assert service.make(db, "example", "missing") is None
        assert service.make(db, "nope", "abc") is None
    body = client.get("/share/example/abc").json()
    assert body["square_image_url"].endswith("size=square") and body["text"] == "Beat me!"
    assert client.get("/share/example/missing").status_code == 404
    assert client.get("/share/nope/abc").status_code == 404


def test_pictures_come_in_both_sizes(env):
    client, _ = env
    for size, expected in (("wide", (1200, 630)), ("square", (1080, 1080))):
        res = client.get(f"/share/example/abc/card.png?size={size}")
        assert res.status_code == 200 and res.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(res.content)).size == expected
    assert client.get("/share/example/abc/card.png?size=huge").status_code == 422


def test_a_picture_is_drawn_once_until_the_card_changes(env, monkeypatch):
    client, _ = env
    drawn = []
    real = render.draw
    monkeypatch.setattr(render, "draw", lambda card, size: drawn.append(size) or real(card, size))
    first = client.get("/share/example/abc/card.png").content
    assert client.get("/share/example/abc/card.png").content == first and drawn == ["wide"]
    state["title"] = "Loki scored 5,000"
    assert client.get("/share/example/abc/card.png").content != first and drawn == ["wide", "wide"]


def test_long_text_still_draws(env):
    client, _ = env
    state["title"] = "A very long title " * 20
    assert client.get("/share/example/abc/card.png?size=square").status_code == 200


def test_page_has_preview_tags_and_sends_people_on(env):
    client, _ = env
    page = client.get("/share/example/abc/page")
    assert page.status_code == 200
    assert 'property="og:image"' in page.text and "/share/example/abc/card.png" in page.text
    assert 'content="noindex, nofollow"' in page.text and 'http-equiv="refresh" content="0;url=/games/relic-hunter"' in page.text
    missing = client.get("/share/example/missing/page")
    assert missing.status_code == 404 and "Nothing here" in missing.text


def test_a_card_cannot_send_people_to_another_site(env):
    client, _ = env
    for bad in ("//evil.example", "https://evil.example"):
        state["target"] = bad
        assert 'url=/"' in client.get("/share/example/abc/page").text


def test_tournament_result_card(env):
    client, Session = env
    with Session() as db:
        ada, tunde = User(email="a@example.com", name="Ada"), User(email="t@example.com", name="Tunde")
        db.add_all([ada, tunde])
        db.flush()
        db.add(Game(name="lane-dash", owner_id=ada.id, repo_url="x"))
        t = Tournament(starts_at=datetime(2026, 10, 20, 17, tzinfo=timezone.utc), ends_at=datetime(2026, 10, 21, 17, tzinfo=timezone.utc))
        db.add(t)
        db.flush()
        db.add(TournamentGame(tournament_id=t.id, game_name="lane-dash", position=1))
        db.add_all([TournamentPoints(tournament_id=t.id, user_id=ada.id, points=22, firsts=1, place=1),
                    TournamentPoints(tournament_id=t.id, user_id=tunde.id, points=9, firsts=0, place=2)])
        db.commit()
        winner, second = f"{t.id}.{ada.id}", f"{t.id}.{tunde.id}"
    card = client.get(f"/share/tournament_result/{winner}").json()
    assert "won" in card["text"]
    page = client.get(f"/share/tournament_result/{second}/page").text
    assert "Tunde came 2nd of 2" in page
    assert client.get(f"/share/tournament_result/{t.id}.9999").status_code == 404
    assert client.get("/share/tournament_result/garbage").status_code == 404
    assert client.get(f"/share/tournament_result/{second}/card.png").status_code == 200
