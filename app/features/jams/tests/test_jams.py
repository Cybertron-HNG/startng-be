from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import manifest
from app.config import settings
from app.db import Base, get_db
from app.features.jams import server_page, service
from app.features.jams.models import Jam, JamEntry, JamVote
from app.features.progress import service as progress
from app.main import app
from app.models import Game, Score, User
from app.routers import pages
from app.security import create_token

SHELL = '<!doctype html><html><head><!--seo--><title>x</title><!--/seo--></head><body><div id="root"></div></body></html>'
NOW = datetime.now(timezone.utc)
GAMES = ["coin-rush", "danfo-dash", "jollof-jump", "hidden-one"]


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

    monkeypatch.setattr(settings, "features", "jams")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("boss", "ada", "tunde", "bola")]
        db.add_all(users)
        db.flush()
        owners = {"coin-rush": users[1], "danfo-dash": users[2], "jollof-jump": users[2], "hidden-one": users[1]}
        db.add_all([Game(name=n, owner_id=owners[n].id, repo_url=f"https://github.com/x/{n}") for n in GAMES])
        db.commit()
        db.get(Game, "hidden-one").hidden = "flagged"
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def make_jam(Session, phase="open", slug="lagos-rush-hour"):
    starts = {"soon": NOW + timedelta(days=1), "open": NOW - timedelta(days=1), "voting": NOW - timedelta(days=12), "over": NOW - timedelta(days=30)}[phase]
    with Session() as db:
        jam = Jam(slug=slug, title="Lagos rush hour", brief="Make a game about getting across Lagos at 6pm.", opens_at=starts,
                  entries_close_at=starts + timedelta(days=10), voting_ends_at=starts + timedelta(days=17))
        db.add(jam)
        db.commit()
        return jam.id


def enter(Session, jam_id, *names):
    with Session() as db:
        db.add_all([JamEntry(jam_id=jam_id, game_name=n) for n in names])
        db.commit()


def played(Session, user_id, game):
    with Session() as db:
        db.add(Score(user_id=user_id, game_name=game, best=10, stats=None))
        db.commit()


def test_publishing_with_a_jam_key_enters_the_game_only_while_the_jam_is_open(env):
    _, _, _, Session = env
    open_id = make_jam(Session, "open", "open-jam")
    make_jam(Session, "voting", "closed-jam")
    with Session() as db:
        for game, slug in [("coin-rush", "open-jam"), ("danfo-dash", "closed-jam"), ("jollof-jump", "no-such-jam")]:
            manifest.apply_sections(db, db.get(Game, game), manifest.parse_sections(f'{{"jam": "{slug}"}}'.encode()))
        manifest.apply_sections(db, db.get(Game, "coin-rush"), {"jam": "open-jam"})  # publishing again doesn't enter it twice
        db.commit()
        assert [e.game.name for e in service.entries(db, db.get(Jam, open_id))] == ["coin-rush"]
        assert db.query(JamEntry).count() == 1
    for bad in ('{"jam": 5}', '{"jam": "Lagos Rush"}', '{"jam": "x"}'):
        with pytest.raises(manifest.SchemaError):
            manifest.parse_sections(bad.encode())


def test_who_can_vote_and_when(env):
    client, tokens, ids, Session = env
    jam = make_jam(Session, "voting")
    enter(Session, jam, "coin-rush", "danfo-dash", "hidden-one")
    vote = lambda who, game: client.post("/jams/lagos-rush-hour/votes", json={"game": game}, headers=tokens[who])

    assert client.post("/jams/lagos-rush-hour/votes", json={"game": "coin-rush"}).status_code == 401
    r = vote("bola", "coin-rush")
    assert r.status_code == 403 and "Play it first" in r.json()["detail"]
    assert vote("ada", "coin-rush").json()["detail"] == "You can't vote for your own game"
    assert vote("bola", "jollof-jump").json()["detail"] == "That game isn't in this jam"  # not entered
    assert vote("bola", "hidden-one").json()["detail"] == "That game isn't in this jam"  # hidden since

    played(Session, ids["bola"], "coin-rush")
    with Session() as db:
        progress.record_open(db, ids["bola"], "danfo-dash")  # opened from start.ng counts as played too
        db.commit()
    assert vote("bola", "coin-rush").status_code == 201
    assert vote("bola", "coin-rush").status_code == 409
    assert vote("bola", "danfo-dash").status_code == 201

    page = client.get("/jams/lagos-rush-hour", headers=tokens["bola"]).json()
    assert [e["game"] for e in page["entries"]] == ["coin-rush", "danfo-dash"]  # hidden one left out
    assert all(e["votes"] is None and e["voted"] for e in page["entries"])  # no counts until it's over
    assert client.delete("/jams/lagos-rush-hour/votes/danfo-dash", headers=tokens["bola"]).status_code == 204
    assert [e["voted"] for e in client.get("/jams/lagos-rush-hour", headers=tokens["bola"]).json()["entries"]] == [True, False]


def test_no_votes_outside_voting_week(env):
    client, tokens, ids, Session = env
    for phase, slug in [("open", "open-jam"), ("over", "old-jam")]:
        enter(Session, make_jam(Session, phase, slug), "coin-rush")
        if phase == "open":
            played(Session, ids["bola"], "coin-rush")
        r = client.post(f"/jams/{slug}/votes", json={"game": "coin-rush"}, headers=tokens["bola"])
        assert r.status_code == 403 and "voting week" in r.json()["detail"]


def test_results_once_voting_is_over(env):
    client, tokens, ids, Session = env
    jam = make_jam(Session, "over")
    enter(Session, jam, "coin-rush", "danfo-dash", "jollof-jump")
    with Session() as db:
        db.add_all([JamVote(jam_id=jam, game_name="danfo-dash", user_id=ids[u]) for u in ("ada", "bola", "boss")])
        db.add_all([JamVote(jam_id=jam, game_name="coin-rush", user_id=ids[u]) for u in ("tunde", "bola")])
        db.commit()
    page = client.get("/jams/lagos-rush-hour").json()
    assert [(e["game"], e["votes"], e["winner"]) for e in page["entries"]] == [("danfo-dash", 3, 1), ("coin-rush", 2, 2), ("jollof-jump", 0, None)]
    assert page["jam"]["phase"] == "over" and page["jam"]["entries"] == 3
    assert page["entries"][0]["why_not"] == "Sign in to vote"


def test_admins_make_jams_with_dates_in_order(env):
    client, tokens, _, _ = env
    body = {"slug": "jollof-jam", "title": "Jollof", "brief": "A game about the best jollof in town.",
            "opens_at": (NOW + timedelta(days=1)).isoformat(), "entries_close_at": (NOW + timedelta(days=11)).isoformat(),
            "voting_ends_at": (NOW + timedelta(days=18)).isoformat()}
    assert client.post("/admin/jams", json=body, headers=tokens["ada"]).status_code == 404  # not an admin: admin routes stay hidden
    r = client.post("/admin/jams", json=body, headers=tokens["boss"])
    assert r.status_code == 201 and r.json()["phase"] == "soon"
    assert client.post("/admin/jams", json=body, headers=tokens["boss"]).status_code == 409
    assert client.post("/admin/jams", json={**body, "slug": "other-jam", "voting_ends_at": body["opens_at"]}, headers=tokens["boss"]).status_code == 422
    r = client.patch("/admin/jams/jollof-jam", json={"opens_at": (NOW - timedelta(days=1)).isoformat()}, headers=tokens["boss"])
    assert r.status_code == 200 and r.json()["phase"] == "open"
    assert client.patch("/admin/jams/jollof-jam", json={"voting_ends_at": (NOW - timedelta(days=5)).isoformat()}, headers=tokens["boss"]).status_code == 422
    assert [j["slug"] for j in client.get("/jams").json()] == ["jollof-jam"]


def test_the_server_written_page_matches_the_app(env):
    client, _, _, Session = env
    make_jam(Session, "open")
    with Session() as db:
        jam = service.by_slug(db, "lagos-rush-hour")
        enter(Session, jam.id, "coin-rush")
        close = server_page.day(jam.entries_close_at)
    html = client.get("/pages/jam/lagos-rush-hour").text
    assert "<h1>Lagos rush hour</h1>" in html and "Make a game about getting across Lagos at 6pm." in html
    assert f"Taking entries until {close}." in html
    assert f'To enter, publish your game with &quot;jam&quot;: &quot;lagos-rush-hour&quot; in its startng.json before {close}.' in html
    assert '<h2>Entries (1)</h2>' in html and '<a href="/games/coin-rush">Coin Rush</a> <span>by Ada</span>' in html
    assert '<link rel="canonical" href="' in html and "/jam/lagos-rush-hour" in html
    missing = client.get("/pages/jam/nope-jam")
    assert missing.status_code == 404 and "noindex" in missing.text and "No jam here" in missing.text


def test_day_and_sitemap(env, monkeypatch):
    _, _, _, Session = env
    assert server_page.day(datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc)) == "5 Nov 2026"
    make_jam(Session, "open", "open-jam")
    make_jam(Session, "soon", "soon-jam")
    with Session() as db:
        assert server_page.sitemap_urls(db) == [("/jam/open-jam", None)]
        monkeypatch.setattr(settings, "features", "")
        assert server_page.sitemap_urls(db) == []


def test_ships_dark(env, monkeypatch):
    client, _, _, Session = env
    make_jam(Session, "open")
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/jams").status_code == 404
    assert client.get("/pages/jam/lagos-rush-hour").status_code == 404
