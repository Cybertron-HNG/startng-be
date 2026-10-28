import io
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.share_cards import render, service
from app.main import app
from app.models import Game, Tournament, TournamentGame, TournamentPoints, User
from app.routers import pages

SHELL = '<html><head><!--seo--><title>start.ng</title><!--/seo--></head><body><div id="root"></div></body></html>'


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
    monkeypatch.setattr(settings, "api_url", "https://api.start.ng")
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        ada = User(email="ada@example.com", name="Ada", character={"name": "Loki <3"})
        db.add(ada)
        db.flush()
        db.add_all([Game(name=f"relic-hunter-{i}", owner_id=ada.id, repo_url=f"https://github.com/ada/g{i}") for i in range(3)])
        start = datetime(2026, 10, 19, 17, tzinfo=timezone.utc)
        t = Tournament(starts_at=start, ends_at=start + timedelta(days=1), finished_at=start + timedelta(days=1), winner_id=ada.id)
        t.games = [TournamentGame(game_name=f"relic-hunter-{i}", position=i) for i in range(3)]
        db.add(t)
        db.flush()
        db.add(TournamentPoints(tournament_id=t.id, user_id=ada.id, points=22, firsts=2, place=1))
        db.commit()
        ids = (t.id, ada.id)
    yield TestClient(app), Session, f"{ids[0]}-{ids[1]}"
    app.dependency_overrides.clear()


def test_off_without_its_flag(env, monkeypatch):
    client, Session, rid = env
    monkeypatch.setattr(settings, "features", "")
    assert client.get(f"/share/tournament_result/{rid}").status_code == 404
    with Session() as db:
        assert service.make(db, "tournament_result", rid) is None  # callers hide their Share button


def test_make_gives_the_link_words_and_pictures(env):
    _, Session, rid = env
    with Session() as db:
        s = service.make(db, "tournament_result", rid)
    assert s.url == f"https://start.ng/s/tournament_result/{rid}"
    assert s.text.startswith("I placed 1st in the start.ng daily tournament with 22 points")
    assert s.image_url.startswith(f"https://api.start.ng/share/tournament_result/{rid}/card.jpg?v=")
    assert s.square_image_url == s.image_url + "&size=square"


def test_the_picture_address_changes_with_the_card(env):
    client, Session, rid = env
    before = client.get(f"/share/tournament_result/{rid}").json()["image_url"]
    with Session() as db:
        db.query(TournamentPoints).update({"points": 30})
        db.commit()
    after = client.get(f"/share/tournament_result/{rid}").json()
    assert after["image_url"] != before  # chat apps never keep showing the old numbers
    assert "30 points" in after["text"]


@pytest.mark.parametrize("path", ["/share/tournament_result/999-1", "/share/tournament_result/nope",
                                  "/share/tournament_result/1-2-3", "/share/challenge/abc"])
def test_nothing_to_share_yet_is_null_not_404(env, path):
    client, _, _ = env
    res = client.get(path)
    assert res.status_code == 200 and res.json() is None  # the Share button hides; the browser logs no error
    assert client.get(f"{path}/card.jpg").status_code == 404  # there's still no picture to serve


def test_an_unknown_kind_is_404(env):
    client, _, _ = env
    assert client.get("/share/nonsense/1").status_code == 404
    assert client.get("/share/nonsense/1/card.jpg").status_code == 404


def test_an_unfinished_tournament_has_no_card(env):
    client, Session, rid = env
    with Session() as db:
        db.query(Tournament).update({"finished_at": None})
        db.commit()
    res = client.get(f"/share/tournament_result/{rid}")
    assert res.status_code == 200 and res.json() is None


@pytest.mark.parametrize("size,dims", [("wide", (1200, 630)), ("square", (1080, 1080))])
def test_card_sizes(env, size, dims):
    client, _, rid = env
    res = client.get(f"/share/tournament_result/{rid}/card.jpg", params={"size": size})
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert res.headers["access-control-allow-origin"] == "*"  # phones fetch it to attach, even after an <img> cached it
    assert Image.open(io.BytesIO(res.content)).size == dims
    assert len(res.content) < 300_000  # WhatsApp skips big preview pictures


def test_a_cached_card_is_quick(env):
    client, _, rid = env
    url = f"/share/tournament_result/{rid}/card.jpg"
    first = client.get(url).content
    started = time.perf_counter()
    assert client.get(url).content == first
    assert time.perf_counter() - started < 0.3  # PRD: under 300 ms once cached


def test_preview_page_has_the_card_in_its_tags(env):
    client, _, rid = env
    res = client.get(f"/share/tournament_result/{rid}/page")
    assert res.status_code == 200
    html = res.text
    assert f'<meta property="og:image" content="https://api.start.ng/share/tournament_result/{rid}/card.jpg?v=' in html
    assert 'content="noindex, nofollow"' in html
    assert "<h1>Loki &lt;3 placed 1st</h1>" in html and "Loki <3" not in html  # names are escaped
    assert "Think you can beat 22 points?" in html
    assert 'href="/tournament"' in html


def test_preview_page_for_a_missing_card(env):
    client, _, _ = env
    res = client.get("/share/tournament_result/999-1/page")
    assert res.status_code == 404 and "noindex" in res.text


def test_registering_kinds(monkeypatch):
    monkeypatch.setattr(service, "_describers", {})
    with pytest.raises(KeyError):
        service.register("chalenge", lambda db, id: None)  # a typo fails loudly
    first = lambda db, id: None  # noqa: E731
    service.register("challenge", first)
    service.register("challenge", first)  # the same one again is fine (reloads)
    with pytest.raises(ValueError):
        service.register("challenge", lambda db, id: None)  # two features can't describe one kind


def test_the_tournament_card(env):
    _, Session, rid = env
    with Session() as db:
        card = service.describe(db, "tournament_result", rid)
    assert (card.name, card.title, card.headline) == ("Loki <3", "placed 1st", "Loki <3 placed 1st")
    assert card.eyebrow == "Daily tournament · Tue 20 Oct"  # the day it ended, Nigeria time
    assert card.facts == [("22", "points"), ("3", "games")] and card.game == "Relic Hunter 0 +2"
    assert card.badge == service.Badge("1", "st", "place", "gold")
    assert card.cta == "Think you can beat 22 points?"


@pytest.mark.parametrize("size", ["wide", "square"])
def test_any_card_draws(size):
    """Long words, no badge, a badge with a big value, nothing optional: the layout copes with whatever a feature describes."""
    long = service.Card(
        name="Supercalifragilisticexpialidocious " * 3, title="a really very long title that goes on and on " * 2,
        eyebrow="League · Weekly · " * 6, facts=[("1,234,567", "points"), ("99", "games"), ("12", "wins"), ("x", "y")],
        game="A game with a very long name indeed " * 3, cta="Think you can beat this very long call to action? " * 3,
        path="/league", text="t",
    )
    ringed = service.Badge("1234", "th", "place a long label")
    bare = service.Card(title="Hi", path="/", text="t")
    for card in (long, bare, service.Card(title="Hi", path="/", text="t", badge=ringed)):
        assert Image.open(io.BytesIO(render.draw(card, size))).size == render.SIZES[size]
