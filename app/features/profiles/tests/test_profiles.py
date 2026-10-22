import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.profiles import badges
from app.features.progress import service as progress
from app.main import app
from app.models import Favorite, Game, Score, User
from app.security import create_token

BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
GAMES = [f"game-{i}" for i in range(10)]


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

    monkeypatch.setattr(settings, "features", "progress,profiles")
    monkeypatch.setattr(progress, "_rules", {"profiles": list(badges.RULES)})
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("ada", "tunde")}
        users["ada"].character = {"name": "Runner Ada", "style": {}}
        db.add_all(users.values())
        db.flush()
        db.add_all([Game(name=g, owner_id=users["tunde"].id, repo_url=f"https://github.com/t/{g}", leaderboard=BOARD) for g in GAMES])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def play(Session, user_id, game, score):
    with Session() as db:
        row = db.query(Score).filter_by(user_id=user_id, game_name=game).first()
        if row is None:
            db.add(Score(user_id=user_id, game_name=game, best=score, stats={"score": score}))
        db.commit()
        return progress.after_run(db, user_id, game, {"score": score})


def test_a_page_has_the_character_level_and_never_the_email(env):
    client, tokens, ids, Session = env
    play(Session, ids["ada"], "game-0", 4200)
    page = client.get(f"/players/{ids['ada']}/profile")
    assert page.status_code == 200
    body = page.json()
    assert body["name"] == "Runner Ada" and body["slug"] == f"{ids['ada']}-runner-ada" and body["level"] == 1
    assert body["games_played"] == 1 and body["me"] is False and body["settings"] is None
    assert body["bests"] == [{"game": "game-0", "title": "Game 0", "best": "4,200", "rank": 1}]
    for response in (page, client.get("/me/profile", headers=tokens["ada"]), client.get("/players/9999/profile")):
        assert "ada@example.com" not in response.text and '"email"' not in response.text


def test_badges_are_awarded_once_and_show_on_the_page(env):
    client, tokens, ids, Session = env
    assert play(Session, ids["ada"], "game-0", 10).unlocked == [{"kind": "badge", "key": "first_score"}]
    assert play(Session, ids["ada"], "game-0", 20).unlocked == []  # once
    page = client.get(f"/players/{ids['ada']}/profile").json()
    assert [b["key"] for b in page["badges"]] == ["first_score"] and page["badges"][0]["name"] == "First score"
    for g in GAMES[1:]:
        play(Session, ids["ada"], g, 5)
    page = client.get(f"/players/{ids['ada']}/profile").json()
    assert {b["key"] for b in page["badges"]} == {"first_score", "ten_games"}
    assert [b["key"] for b in client.get("/badges").json()] == [b.key for b in badges.CATALOG]
    assert "daily_challenger" in {b.key for b in badges.CATALOG}  # earned in the daily challenge (A2), shown here


def test_the_badge_rules(env):
    rule = {r.key: r.check for r in badges.RULES}
    snap = progress.Snapshot(runs=1, distinct_games=10, current_streak=3, best_streak=7, streak_freezes=0, xp=0, level=1,
                             tournament_wins=1, ranks={"game-0": 10})
    assert all(check(snap, None) for check in rule.values())
    none = progress.Snapshot(runs=0, distinct_games=9, current_streak=0, best_streak=2, streak_freezes=0, xp=0, level=1,
                             tournament_wins=0, ranks={"game-0": 11})
    assert not any(check(none, None) for check in rule.values())


def test_a_private_page_is_a_404_to_everyone_else(env):
    client, tokens, ids, Session = env
    play(Session, ids["ada"], "game-0", 10)
    assert client.get(f"/players/{ids['ada']}/progress").status_code == 200  # the Progress Engine's public view
    changed = client.put("/me/profile-settings", json={"public": False}, headers=tokens["ada"]).json()
    assert changed["public"] is False
    assert client.get(f"/players/{ids['ada']}/profile").status_code == 404
    assert client.get(f"/players/{ids['ada']}/profile", headers=tokens["tunde"]).status_code == 404
    assert client.get(f"/players/{ids['ada']}/progress").status_code == 404  # D1 follows the same choice
    mine = client.get(f"/players/{ids['ada']}/profile", headers=tokens["ada"])
    assert mine.status_code == 200 and mine.json()["me"] is True and mine.json()["settings"]["public"] is False
    assert client.get("/me/profile", headers=tokens["ada"]).status_code == 200
    client.put("/me/profile-settings", json={"public": True}, headers=tokens["ada"])
    assert client.get(f"/players/{ids['ada']}/profile").status_code == 200


def test_favorites_and_ranks_can_be_hidden(env):
    client, tokens, ids, Session = env
    play(Session, ids["ada"], "game-0", 10)
    with Session() as db:
        db.add_all([Favorite(user_id=ids["ada"], key="g:game-1"), Favorite(user_id=ids["ada"], key="i:7"), Favorite(user_id=ids["ada"], key="g:gone")])
        db.commit()
    page = client.get(f"/players/{ids['ada']}/profile").json()
    assert page["favorites"] == [{"game": "game-1", "title": "Game 1"}] and page["bests"][0]["game"] == "game-0"
    client.put("/me/profile-settings", json={"show_favorites": False, "show_ranks": False}, headers=tokens["ada"])
    page = client.get(f"/players/{ids['ada']}/profile").json()
    assert page["favorites"] is None and page["bests"] is None


def test_hidden_games_dont_show_and_defaults_apply_to_new_players(env):
    client, tokens, ids, Session = env
    play(Session, ids["ada"], "game-0", 10)
    with Session() as db:
        db.get(Game, "game-0").hidden = "reported"
        db.commit()
    assert client.get(f"/players/{ids['ada']}/profile").json()["bests"] == []
    fresh = client.get("/me/profile", headers=tokens["tunde"]).json()["settings"]
    assert fresh == {"public": True, "show_favorites": True, "show_ranks": True, "notice_seen": False}
    assert client.put("/me/profile-settings", json={"notice_seen": True}, headers=tokens["tunde"]).json()["notice_seen"] is True
    assert client.put("/me/profile-settings", json={}).status_code == 401
