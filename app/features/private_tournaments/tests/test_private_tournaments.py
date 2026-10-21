from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tournaments
from app.config import settings
from app.db import Base, get_db
from app.features.private_tournaments import service
from app.features.private_tournaments.models import PrivateTournament, PrivateTournamentScore
from app.main import app
from app.models import Game, User
from app.security import create_token

BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
PLAYERS = ("ada", "tunde", "chioma", "musa", "kofi")


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
    monkeypatch.setattr(service, "SessionLocal", Session)  # score events open their own session
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in PLAYERS]
        db.add_all(users)
        db.flush()
        db.add_all([Game(name=f"g{i}", owner_id=users[0].id, repo_url=f"https://github.com/ada/g{i}", leaderboard=BOARD) for i in range(6)])
        db.add(Game(name="no-board", owner_id=users[0].id, repo_url="x"))
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def make(client, tokens, who="ada", games=("g0", "g1"), hours=48, title="Family Cup"):
    return client.post("/private-tournaments", json={"title": title, "games": list(games), "hours": hours}, headers=tokens[who])


def play(client, tokens, who, game, score):
    assert client.post(f"/games/{game}/scores", json={"score": score}, headers=tokens[who]).status_code == 200


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, _, _ = env
    monkeypatch.setattr(settings, "features", "")
    assert make(client, tokens).status_code == 404


def test_create_and_the_maker_is_a_member(env):
    client, tokens, _, _ = env
    res = make(client, tokens)
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["title"], body["games"], body["members"], body["joined"], body["mine"]) == ("Family Cup", ["g0", "g1"], 1, True, True)
    assert len(body["code"]) >= 11  # long enough that a link can't be guessed


@pytest.mark.parametrize("games,hours,code", [
    ([], 24, 422), (["g0", "g1", "g2", "g3", "g4", "g5"], 24, 422),  # 1 to 5 games
    (["g0"], 23, 422), (["g0"], 721, 422),  # 1 day to 30 days
    (["no-board"], 24, 400), (["nope"], 24, 400),  # needs a leaderboard and to exist
])
def test_create_rules(env, games, hours, code):
    client, tokens, _, _ = env
    assert make(client, tokens, games=games, hours=hours).status_code == code


def test_the_same_game_twice_is_once(env):
    client, tokens, _, _ = env
    assert make(client, tokens, games=["g0", "g0"]).json()["games"] == ["g0"]


def test_at_most_five_running_at_once(env):
    client, tokens, _, _ = env
    for _ in range(5):
        assert make(client, tokens).status_code == 201
    assert make(client, tokens).status_code == 400


def test_link_shows_basics_but_standings_only_to_members(env):
    client, tokens, _, _ = env
    code = make(client, tokens).json()["code"]
    outsider = client.get(f"/private-tournaments/{code}", headers=tokens["tunde"]).json()
    assert (outsider["joined"], outsider["title"], outsider["owner"]) == (False, "Family Cup", "Ada")
    assert "standings" not in outsider and "game_boards" not in outsider
    assert "standings" not in client.get(f"/private-tournaments/{code}").json()  # signed out too
    assert client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"]).status_code == 200
    member = client.get(f"/private-tournaments/{code}", headers=tokens["tunde"]).json()
    assert member["joined"] and member["members"] == 2 and "standings" in member


def test_join_needs_sign_in_and_a_real_link(env):
    client, tokens, _, _ = env
    code = make(client, tokens).json()["code"]
    assert client.post(f"/private-tournaments/{code}/join").status_code == 401
    assert client.post("/private-tournaments/nope/join", headers=tokens["tunde"]).status_code == 404
    assert client.get("/private-tournaments/nope").status_code == 404


def test_joining_twice_is_fine(env):
    client, tokens, _, _ = env
    code = make(client, tokens).json()["code"]
    for _ in range(2):
        assert client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"]).json()["members"] == 2


def test_only_members_runs_inside_the_window_count(env):
    client, tokens, _, _ = env
    code = make(client, tokens).json()["code"]
    play(client, tokens, "tunde", "g0", 900)  # not a member yet
    client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"])
    client.post(f"/private-tournaments/{code}/join", headers=tokens["chioma"])
    play(client, tokens, "ada", "g0", 100)
    play(client, tokens, "tunde", "g0", 300)
    play(client, tokens, "chioma", "g0", 200)
    play(client, tokens, "chioma", "g5", 999)  # a game that isn't in it
    play(client, tokens, "musa", "g0", 5000)  # never joined
    body = client.get(f"/private-tournaments/{code}", headers=tokens["ada"]).json()
    assert [(s["name"], s["points"]) for s in body["standings"]] == [("Tunde", 10), ("Chioma", 7), ("Ada", 5)]
    top = next(g for g in body["game_boards"] if g["name"] == "g0")["top"]
    assert [r["stats"]["score"] for r in top] == [300, 200, 100]
    assert next(g for g in body["game_boards"] if g["name"] == "g1")["top"] == []


def test_keeps_the_best_run_and_points_add_across_games(env):
    client, tokens, _, _ = env
    code = make(client, tokens).json()["code"]
    client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"])
    play(client, tokens, "ada", "g0", 500)
    play(client, tokens, "ada", "g0", 100)  # worse: ignored
    play(client, tokens, "tunde", "g0", 400)
    play(client, tokens, "tunde", "g1", 50)
    standings = client.get(f"/private-tournaments/{code}", headers=tokens["tunde"]).json()["standings"]
    assert [(s["name"], s["points"], s["firsts"]) for s in standings] == [("Tunde", 17, 1), ("Ada", 10, 1)]


def test_nothing_counts_after_it_ends_or_gives_global_points(env):
    client, tokens, _, Session = env
    code = make(client, tokens).json()["code"]
    play(client, tokens, "ada", "g0", 100)
    with Session() as db:
        t = db.scalar(select(PrivateTournament))
        t.ends_at = tournaments.now() - timedelta(minutes=1)
        t.starts_at = t.ends_at - timedelta(hours=2)
        db.commit()
    play(client, tokens, "ada", "g0", 900)
    body = client.get(f"/private-tournaments/{code}", headers=tokens["ada"]).json()
    assert body["finished"] is True and [s["points"] for s in body["standings"]] == [10]
    assert client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"]).status_code == 400  # too late
    assert client.get("/tournaments/leaderboard").json() == []  # no global points


def test_leaving_takes_your_runs_with_you_but_the_maker_cannot_leave(env):
    client, tokens, _, Session = env
    code = make(client, tokens).json()["code"]
    client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"])
    play(client, tokens, "tunde", "g0", 300)
    assert client.delete(f"/private-tournaments/{code}/members/me", headers=tokens["tunde"]).status_code == 204
    with Session() as db:
        assert db.scalars(select(PrivateTournamentScore)).all() == []
    assert client.delete(f"/private-tournaments/{code}/members/me", headers=tokens["ada"]).status_code == 400


def test_only_the_maker_deletes(env):
    client, tokens, _, _ = env
    code = make(client, tokens).json()["code"]
    client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"])
    assert client.delete(f"/private-tournaments/{code}", headers=tokens["tunde"]).status_code == 403
    assert client.delete(f"/private-tournaments/{code}", headers=tokens["ada"]).status_code == 204
    assert client.get(f"/private-tournaments/{code}").status_code == 404


def test_my_tournaments_running_first(env):
    client, tokens, _, Session = env
    old = make(client, tokens, title="Old one").json()["code"]
    new = make(client, tokens, title="New one").json()["code"]
    with Session() as db:
        t = db.scalar(select(PrivateTournament).where(PrivateTournament.code == old))
        t.ends_at = tournaments.now() - timedelta(hours=1)
        db.commit()
    assert [t["code"] for t in client.get("/private-tournaments/mine", headers=tokens["ada"]).json()] == [new, old]
    assert client.get("/private-tournaments/mine", headers=tokens["tunde"]).json() == []


def test_member_cap(env, monkeypatch):
    client, tokens, _, _ = env
    monkeypatch.setattr(service, "MAX_MEMBERS", 2)
    code = make(client, tokens).json()["code"]
    assert client.post(f"/private-tournaments/{code}/join", headers=tokens["tunde"]).status_code == 200
    assert client.post(f"/private-tournaments/{code}/join", headers=tokens["chioma"]).status_code == 400


SHELL = '<html><head><!--seo--><title>start.ng</title><!--/seo--></head><body><div id="root"></div></body></html>'


def test_invite_card_is_a_png_anyone_with_the_link_can_load(env):
    client, tokens, _, _ = env
    code = make(client, tokens, title="Family Cup").json()["code"]
    res = client.get(f"/private-tournaments/{code}/card.png")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/private-tournaments/nope-nope-1/card.png").status_code == 404


def test_invite_page_has_the_tournament_in_its_preview_tags(env, monkeypatch):
    from app.routers import pages
    client, tokens, _, _ = env
    monkeypatch.setattr(pages, "_get_shell", lambda: SHELL)
    code = make(client, tokens, title="Family <Cup>").json()["code"]
    html = client.get(f"/private-tournaments/{code}/page").text
    assert "Family &lt;Cup&gt; · tournament with friends" in html and "<Cup>" not in html  # escaped
    assert f"/private-tournaments/{code}/card.png" in html and f"/friends/{code}" in html
    assert "noindex" in html  # a private link stays out of search
    missing = client.get("/private-tournaments/nope-nope-1/page")
    assert missing.status_code == 404 and "noindex" in missing.text


def test_the_maker_can_swap_the_invite_link(env):
    client, tokens, _, _ = env
    old = make(client, tokens).json()["code"]
    client.post(f"/private-tournaments/{old}/join", headers=tokens["tunde"])
    assert client.post(f"/private-tournaments/{old}/new-link", headers=tokens["tunde"]).status_code == 403
    new = client.post(f"/private-tournaments/{old}/new-link", headers=tokens["ada"]).json()["code"]
    assert new != old
    assert client.get(f"/private-tournaments/{old}").status_code == 404  # the old link is dead
    assert client.post(f"/private-tournaments/{old}/join", headers=tokens["chioma"]).status_code == 404
    assert client.get(f"/private-tournaments/{new}", headers=tokens["tunde"]).json()["members"] == 2  # members stay


def test_card_shows_the_games_covers_and_copes_when_one_is_missing(env, monkeypatch):
    import io
    from PIL import Image
    from app.features.private_tournaments import share
    client, tokens, _, Session = env
    code = make(client, tokens, games=["g0", "g1"]).json()["code"]
    with Session() as db:
        for g in db.scalars(select(Game).where(Game.name.in_(["g0", "g1"]))):
            g.cover = "cover.webp"
        db.commit()
    asked = []
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 30, 30)).save(buf, "PNG")

    def fake(url):
        asked.append(url)
        return buf.getvalue() if "g0" in url else None  # g1's picture can't be had

    monkeypatch.setattr(share, "_fetch", fake)
    res = client.get(f"/private-tournaments/{code}/card.png")
    assert res.status_code == 200 and len(asked) == 2 and any("g0" in u for u in asked)
    card = Image.open(io.BytesIO(res.content))
    assert card.size == (1200, 630)
    assert card.getpixel((120, 400)) == (200, 30, 30)  # g0's cover is in the first tile


def test_a_tournament_for_a_crew_is_found_on_the_crews_list_by_its_members(env):
    client, tokens, _, _ = env
    crew = client.post("/crews", json={"name": "Lagos Family", "kind": "family"}, headers=tokens["ada"]).json()
    client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"])
    res = client.post("/private-tournaments", json={"title": "Family Cup", "games": ["g0"], "hours": 48, "crew_id": crew["id"]}, headers=tokens["tunde"])
    assert res.status_code == 201 and res.json()["crew_id"] == crew["id"]
    code = res.json()["code"]
    # a crew member who hasn't joined sees it, and can open it and join from there
    listed = client.get(f"/private-tournaments/crew/{crew['id']}", headers=tokens["ada"]).json()
    assert [(t["code"], t["joined"]) for t in listed] == [(code, False)]
    assert client.post(f"/private-tournaments/{code}/join", headers=tokens["ada"]).json()["joined"] is True
    # someone outside the crew gets nothing from the list
    assert client.get(f"/private-tournaments/crew/{crew['id']}", headers=tokens["chioma"]).json() == []


def test_only_a_crew_member_can_start_a_tournament_for_it(env):
    client, tokens, _, _ = env
    crew = client.post("/crews", json={"name": "Lagos Family", "kind": "family"}, headers=tokens["ada"]).json()
    body = {"title": "Family Cup", "games": ["g0"], "hours": 48, "crew_id": crew["id"]}
    assert client.post("/private-tournaments", json=body, headers=tokens["chioma"]).status_code == 400
    assert client.post("/private-tournaments", json={**body, "crew_id": 999}, headers=tokens["ada"]).status_code == 400
