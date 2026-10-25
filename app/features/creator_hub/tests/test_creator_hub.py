from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.creator_hub import service
from app.features.creator_hub.models import GameDailyStat
from app.features.progress.models import Run
from app.features.share_cards import service as share
from app.main import app
from app.models import Game, GamePlay, User
from app.routers import pages
from app.security import create_token

TODAY = date(2026, 10, 22)
NOON = datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc)  # 12:00 in Nigeria
SHELL = '<html><head><!--seo--><title>x</title><!--/seo--></head><body><div id="root"></div></body></html>'


def ago(days: int, hour: int = 10) -> datetime:
    """A moment `days` days before TODAY (Nigeria), at `hour` UTC."""
    d = TODAY - timedelta(days=days)
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc)


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

    monkeypatch.setattr(settings, "features", "creator_hub,share_cards")
    monkeypatch.setattr(service, "now", lambda: NOON)
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("tega", "dele", "ada", "tunde", "chioma")}
        users["tega"].character = {"name": "Tega Games", "style": {}}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name="lane-dash", owner_id=users["tega"].id, repo_url="a"), Game(name="relic", owner_id=users["tega"].id, repo_url="b"),
                    Game(name="secret", owner_id=users["tega"].id, repo_url="c", hidden="reported"), Game(name="other", owner_id=users["dele"].id, repo_url="d")])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def play(db, game, visitor, at):
    db.add(GamePlay(game_name=game, visitor=visitor, at=at))


def run(db, game, user, at, flagged=False):
    db.add(Run(user_id=user, game_name=game, score=10, stats={"score": 10}, created_at=at, flagged=flagged))


def seed(Session, ids):
    with Session() as db:
        # lane-dash: 4 plays today by v1, v1(again), v2; 2 yesterday by v1; 1 ten days ago by v3
        for v in ("v1", "v1", "v2"):
            play(db, "lane-dash", v, ago(0))
        for v in ("v1", "v2"):
            play(db, "lane-dash", v, ago(1))
        play(db, "lane-dash", "v3", ago(10))
        play(db, "relic", "v9", ago(3))
        play(db, "other", "v1", ago(2))
        play(db, "other", "v7", ago(2))
        # runs on lane-dash: ada on 3 days (repeat), tunde on 1 day, chioma flagged (never counted)
        for d in (0, 1, 5):
            run(db, "lane-dash", ids["ada"], ago(d))
        run(db, "lane-dash", ids["tunde"], ago(0))
        run(db, "lane-dash", ids["chioma"], ago(0), flagged=True)
        db.commit()


def test_the_dashboard_matches_the_raw_data(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    with Session() as db:
        d = service.dashboard(db, ids["tega"], TODAY)
    games = {g.game: g for g in d.games}
    lane = games["lane-dash"]
    assert (lane.plays_7, lane.plays_30, lane.plays_all) == (5, 6, 6)
    assert lane.visitors_30 == 3  # v1, v2, v3
    assert lane.players_30 == 2 and lane.repeat_players_30 == 1 and lane.runs_30 == 4  # the flagged run isn't counted
    assert lane.avg_session_seconds is None  # no Kit data: still works
    assert lane.change_7 == 4  # 5 in the last 7 days, 1 (v3, ten days ago) in the 7 before
    assert [t["plays"] for t in lane.trend][-1] == 3 and sum(t["plays"] for t in lane.trend) == 6 and len(lane.trend) == 30
    assert games["relic"].plays_30 == 1 and games["secret"].hidden == "reported"
    assert d.best_game == "lane-dash" and d.plays_30 == 7 and d.visitors_30 == 4
    assert "other" not in games  # only their own games


def test_history_survives_the_raw_plays_being_deleted(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    with Session() as db:
        before = service.dashboard(db, ids["tega"], TODAY)
        wrote = service.rollup_missing(db, TODAY)
        assert wrote > 0 and service.rolled_through(db) == TODAY - timedelta(days=1)  # today isn't over: it stays live
        assert service.rollup_missing(db, TODAY) == 0  # nothing more to do
        again = service.rollup(db, TODAY - timedelta(days=10), TODAY - timedelta(days=1))
        assert again == wrote  # rewriting is safe
        assert db.query(GameDailyStat).count() == wrote
        db.execute(delete(GamePlay).where(GamePlay.at < ago(0)))  # the 30-day purge, early
        db.commit()
        after = service.dashboard(db, ids["tega"], TODAY)
    for a, b in zip(before.games, after.games):
        assert (a.plays_7, a.plays_30, a.plays_all) == (b.plays_7, b.plays_30, b.plays_all) and a.trend == b.trend


def test_a_missed_night_is_filled_in_later(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    with Session() as db:
        service.rollup(db, TODAY - timedelta(days=10), TODAY - timedelta(days=8))
        assert service.rolled_through(db) == TODAY - timedelta(days=8)
        service.rollup_missing(db, TODAY)
        assert service.rolled_through(db) == TODAY - timedelta(days=1)
        assert service.total_plays(db, ["lane-dash"], TODAY) == {"lane-dash": 6}


def test_the_public_page_has_no_email_and_no_hidden_games(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    page = client.get(f"/by/{ids['tega']}")
    assert page.status_code == 200
    body = page.json()
    assert body["name"] == "Tega Games" and body["slug"] == f"{ids['tega']}-tega-games"
    assert sorted(g["name"] for g in body["games"]) == ["lane-dash", "relic"] and body["me"] is False
    assert "example.com" not in page.text and '"email"' not in page.text
    assert client.get(f"/by/{ids['ada']}").status_code == 404  # ada has no games
    assert client.get("/by/99999").status_code == 404


def test_bio_is_cleaned_and_shown(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    assert client.put("/me/creator-bio", json={"bio": "  I make\n\n small   games  "}, headers=tokens["tega"]).json() == {"bio": "I make small games"}
    assert client.get(f"/by/{ids['tega']}").json()["bio"] == "I make small games"
    assert client.put("/me/creator-bio", json={"bio": "x" * 401}, headers=tokens["tega"]).status_code == 422
    assert client.put("/me/creator-bio", json={"bio": ""}, headers=tokens["tega"]).json() == {"bio": None}
    assert client.put("/me/creator-bio", json={"bio": "hi"}).status_code == 401


def test_the_board_ranks_creators_by_different_players(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    board = client.get("/by").json()
    assert [(b["name"], b["games"], b["players_30"]) for b in board] == [("Tega Games", 2, 4), ("Dele", 1, 2)]
    assert "example.com" not in str(board)


def test_the_dashboard_is_private(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    assert client.get("/me/creator-dashboard").status_code == 401
    mine = client.get("/me/creator-dashboard", headers=tokens["tega"]).json()
    assert {g["game"] for g in mine["games"]} == {"lane-dash", "relic", "secret"}
    assert client.get("/me/creator-dashboard", headers=tokens["ada"]).json() == {"games": [], "best_game": None, "plays_7": 0, "plays_30": 0, "visitors_30": 0}


def test_the_page_html_and_share_card(env):
    client, tokens, ids, Session = env
    seed(Session, ids)
    html = client.get(f"/by/{ids['tega']}/page")
    assert html.status_code == 200 and 'property="og:image"' in html.text and f"/share/creator/{ids['tega']}/card.jpg" in html.text
    assert "Tega Games" in html.text and "Lane Dash" in html.text and "Secret" not in html.text
    assert client.get("/by/99999/page").status_code == 404
    with Session() as db:
        made = share.make(db, "creator", str(ids["tega"]))
        assert made.url.endswith(f"/s/creator/{ids['tega']}")
        assert share.describe(db, "creator", str(ids["ada"])) is None
    assert client.get(f"/share/creator/{ids['tega']}/card.jpg").status_code == 200


def test_one_player_is_not_plural_on_the_card(env, monkeypatch):
    client, tokens, ids, Session = env
    seed(Session, ids)
    from app.features.creator_hub import card

    with Session() as db:
        monkeypatch.setattr(service, "unique_visitors", lambda db, games, days=30, today=None: 1)
        assert card.describe(db, str(ids["tega"])).facts[1] == ("1", "player, 30 days")
        monkeypatch.setattr(service, "unique_visitors", lambda db, games, days=30, today=None: 2)
        assert card.describe(db, str(ids["tega"])).facts[1] == ("2", "players, 30 days")


def test_the_page_links_each_game_and_the_sitemap_lists_creators(env, monkeypatch):
    client, tokens, ids, Session = env
    html = client.get(f"/by/{ids['tega']}/page").text
    assert '<li><a href="/games/lane-dash">Lane Dash</a></li>' in html and '<a href="/by">See top creators</a>' in html
    sitemap = client.get("/pages/sitemap.xml").text
    assert "<loc>https://start.ng/by</loc>" in sitemap
    assert f"<loc>https://start.ng/by/{ids['tega']}-tega-games</loc>" in sitemap and f"<loc>https://start.ng/by/{ids['dele']}-dele</loc>" in sitemap
    assert f"/by/{ids['ada']}" not in sitemap  # no games, no page
    monkeypatch.setattr(settings, "features", "")
    assert "/by" not in client.get("/pages/sitemap.xml").text  # off: the pages answer 404, so they aren't listed
