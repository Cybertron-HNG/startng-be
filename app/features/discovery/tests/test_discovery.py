from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import manifest
from app.config import settings
from app.db import Base, get_db
from app.features.discovery import service
from app.features.discovery.models import GameDiscovery, GameRank
from app.features.game_health.models import GameHealth
from app.features.progress.models import GameOpen, Run
from app.main import app
from app.models import Game, GamePlay, User
from app.security import create_token

BOARD = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]


def ago(days=0, hours=0):
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours)


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

    monkeypatch.setattr(settings, "features", "discovery")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = {n: User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in ("boss", "ada", "tunde")}
        db.add_all(users.values())
        db.flush()
        owner = users["ada"].id
        db.add_all([
            Game(name="old-a", owner_id=owner, repo_url="a", created_at=ago(30), cover="c.png", description="d", leaderboard=BOARD, meets_standards=True),
            Game(name="old-b", owner_id=owner, repo_url="b", created_at=ago(30)),
            Game(name="beginner", owner_id=owner, repo_url="c", created_at=ago(1)),
            Game(name="quiet", owner_id=owner, repo_url="d", created_at=ago(20)),
        ])
        db.commit()
        ids = {n: u.id for n, u in users.items()}
        tokens = {n: {"Authorization": f"Bearer {create_token(u.id)}"} for n, u in users.items()}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def busy(db, game, visitors, when=None):
    for i in range(visitors):
        db.add(GamePlay(game_name=game, visitor=f"{game}-{i}", at=when or ago(1)))


def test_a_new_low_traffic_game_shows_under_new_but_not_most_played(env):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-a", 10)
        db.commit()
    shelves = client.get("/discover/shelves").json()
    assert shelves["new"] == ["beginner"]  # a day old, no plays: still there
    assert shelves["most_played"] == ["old-a"]
    assert "beginner" not in shelves["most_played"]


def test_a_game_with_ten_second_sessions_drops_in_most_played(env, monkeypatch):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-a", 10)
        busy(db, "old-b", 12)  # a little more popular ...
        db.commit()
    assert client.get("/discover/shelves").json()["most_played"] == ["old-b", "old-a"]
    # ... until the Kit says its sessions last 10 seconds.
    monkeypatch.setattr(service, "session_seconds", lambda db, game, since: 10 if game == "old-b" else 90)
    with Session() as db:
        service.recompute(db)
        ranks = {r.game_name: r for r in db.scalars(select(GameRank)).all()}
        assert ranks["old-b"].avg_session == 10 and ranks["old-b"].score < ranks["old-a"].score
    assert client.get("/discover/shelves").json()["most_played"] == ["old-a", "old-b"]


def test_ranking_uses_players_runs_and_return_visits(env):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-a", 5)
        busy(db, "old-b", 5)
        for d in (0, 1):  # old-b's player came back on another day
            db.add(Run(user_id=ids["tunde"], game_name="old-b", score=1, stats={"score": 1}, created_at=ago(d)))
        db.add(Run(user_id=ids["tunde"], game_name="old-a", score=1, stats={"score": 1}, created_at=ago(0)))
        db.commit()
        service.recompute(db)
        ranks = {r.game_name: r for r in db.scalars(select(GameRank)).all()}
    assert ranks["old-b"].return_rate == 1.0 and ranks["old-a"].return_rate == 0.0
    assert ranks["old-b"].score > ranks["old-a"].score
    assert (ranks["old-b"].visitors_7, ranks["old-b"].runs_7, ranks["old-b"].players_7) == (5, 2, 1)


def test_most_improved_is_growth_against_the_week_before(env):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-a", 8, ago(10))  # last week
        busy(db, "old-a", 9)           # this week: about the same
        busy(db, "old-b", 1, ago(10))
        busy(db, "old-b", 8)           # 1 -> 8
        busy(db, "quiet", 2)           # too few people to count
        db.commit()
    assert client.get("/discover/shelves").json()["most_improved"] == ["old-b", "old-a"]


def test_owners_see_exactly_why_a_game_is_not_featured(env):
    client, tokens, ids, Session = env
    mine = client.get("/games/old-b/featuring", headers=tokens["ada"]).json()
    failed = {c["key"] for c in mine["checks"] if not c["ok"]}
    assert not mine["eligible"] and failed == {"standards", "cover", "description", "leaderboard", "mobile", "kit"}
    assert next(c for c in mine["checks"] if c["key"] == "kit")["required"] is False
    assert client.get("/games/old-b/featuring", headers=tokens["tunde"]).status_code == 404  # not theirs
    nearly = client.get("/games/old-a/featuring", headers=tokens["ada"]).json()
    assert {c["key"] for c in nearly["checks"] if c["required"] and not c["ok"]} == {"mobile"}


def test_featured_needs_eligibility_staff_picks_do_not(env):
    client, tokens, ids, Session = env
    boss = tokens["boss"]
    assert client.get("/admin/featured", headers=tokens["ada"]).status_code == 404
    refused = client.put("/admin/featured/featured/old-a", headers=boss)
    assert refused.status_code == 409 and "phone" in refused.json()["detail"]
    with Session() as db:  # startng.json says "mobile": true
        manifest.apply_sections(db, db.get(Game, "old-a"), manifest.parse_sections(b'{"mobile": true}'))
        db.commit()
        assert db.get(GameDiscovery, "old-a").mobile is True
    assert client.put("/admin/featured/featured/old-a", headers=boss).status_code == 204
    assert client.put("/admin/featured/staff/old-b", headers=boss).status_code == 204  # staff picks are editorial
    assert client.put("/admin/featured/staff/quiet", headers=boss).status_code == 204
    assert client.put("/admin/featured/other/quiet", headers=boss).status_code == 404
    shelves = client.get("/discover/shelves").json()
    assert shelves["featured"] == ["old-a"] and shelves["staff"] == ["old-b", "quiet"]
    client.post("/admin/featured/staff/quiet/move?direction=up", headers=boss)
    assert client.get("/discover/shelves").json()["staff"] == ["quiet", "old-b"]
    tab = client.get("/admin/featured", headers=boss).json()
    assert [g["game"] for g in tab["featured"]] == ["old-a"] and "old-a" not in [g["game"] for g in tab["candidates"]]
    assert client.delete("/admin/featured/featured/old-a", headers=boss).status_code == 204
    assert client.get("/discover/shelves").json()["featured"] == []


def test_a_failing_game_cannot_be_featured_and_hidden_ones_vanish(env):
    client, tokens, ids, Session = env
    with Session() as db:
        manifest.apply_sections(db, db.get(Game, "old-a"), {"mobile": True})
        db.add(GameHealth(game_name="old-a", status="unhealthy"))
        db.commit()
    assert "Loads" in client.put("/admin/featured/featured/old-a", headers=tokens["boss"]).json()["detail"]
    client.put("/admin/featured/staff/old-b", headers=tokens["boss"])
    with Session() as db:
        db.get(Game, "old-b").hidden = "reported"
        db.commit()
    assert client.get("/discover/shelves").json()["staff"] == []


def test_continue_playing_and_play_next(env):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-a", 10)
        busy(db, "old-b", 6)
        busy(db, "quiet", 2)
        db.add_all([GameOpen(user_id=ids["tunde"], game_name="old-b", first_at=ago(3), last_at=ago(1)),
                    GameOpen(user_id=ids["tunde"], game_name="old-a", first_at=ago(60), last_at=ago(40))])  # too long ago
        db.commit()
    assert client.get("/discover/shelves").json()["continue_playing"] == []  # signed out
    assert client.get("/discover/shelves", headers=tokens["tunde"]).json()["continue_playing"] == ["old-b"]
    nxt = client.get("/discover/next?after=old-a", headers=tokens["tunde"]).json()["games"]
    assert nxt == ["quiet", "beginner", "old-b"]  # games they have not opened first, best ranked first, then the one they have
    assert client.get("/discover/next?after=old-a&limit=3").json()["games"][0] == "old-b"
    assert client.get("/discover/next").status_code == 422


def _tournament(db, names, starts=None, ends=None):
    from app.models import Tournament, TournamentGame

    t = Tournament(starts_at=starts or ago(hours=1), ends_at=ends or (datetime.now(timezone.utc) + timedelta(hours=5)))
    t.games = [TournamentGame(game_name=n, position=i) for i, n in enumerate(names)]
    db.add(t)
    db.commit()


def test_play_next_inside_a_tournament_only_offers_that_tournaments_games(env):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-a", 10)
        busy(db, "quiet", 2)
        db.add(GameOpen(user_id=ids["tunde"], game_name="quiet", first_at=ago(3), last_at=ago(1)))
        _tournament(db, ["old-a", "old-b", "quiet"])
    # in the tournament: only its other games, in its order after this one, those not opened first
    got = client.get("/discover/next?after=old-a", headers=tokens["tunde"]).json()
    assert got == {"games": ["old-b", "quiet"], "tournament": "today's tournament"}
    assert client.get("/discover/next?after=old-b").json()["games"] == ["quiet", "old-a"]  # goes round; signed out is fine
    assert client.get("/discover/next?after=quiet", headers=tokens["tunde"]).json()["games"] == ["old-a", "old-b"]
    # a game that is not in the tournament gets the usual suggestions, which may include tournament games
    outside = client.get("/discover/next?after=beginner", headers=tokens["tunde"]).json()
    assert outside["tournament"] is None and set(outside["games"]) == {"old-a", "old-b", "quiet"}


def test_play_next_ignores_a_tournament_that_is_over_hidden_games_and_a_tournament_of_one(env):
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-b", 4)
        _tournament(db, ["old-a", "old-b"], starts=ago(days=2), ends=ago(days=1))  # over
    assert client.get("/discover/next?after=old-a").json()["tournament"] is None
    with Session() as db:
        db.query(Game).filter(Game.name == "quiet").update({"hidden": "flagged"})
        _tournament(db, ["old-a", "quiet"])  # its only other game is hidden: nothing to offer, so the usual suggestions
    got = client.get("/discover/next?after=old-a").json()
    assert got["tournament"] is None and "quiet" not in got["games"] and got["games"]


def test_play_next_uses_a_private_tournament_only_for_its_members(env, monkeypatch):
    from app.features.private_tournaments.models import PrivateTournament, PrivateTournamentGame, PrivateTournamentMember

    monkeypatch.setattr(settings, "features", "discovery,private_tournaments")
    client, tokens, ids, Session = env
    with Session() as db:
        busy(db, "old-b", 4)
        t = PrivateTournament(code="abc123", owner_id=ids["ada"], title="Our crew", starts_at=ago(hours=1), ends_at=datetime.now(timezone.utc) + timedelta(hours=5))
        t.games = [PrivateTournamentGame(game_name="old-a", position=0), PrivateTournamentGame(game_name="beginner", position=1)]
        t.members = [PrivateTournamentMember(user_id=ids["tunde"])]
        db.add(t)
        db.commit()
    member = client.get("/discover/next?after=old-a", headers=tokens["tunde"]).json()
    assert member == {"games": ["beginner"], "tournament": "Our crew"}
    other = client.get("/discover/next?after=old-a", headers=tokens["ada"]).json()  # not a member: never told its games
    assert other["tournament"] is None
    assert client.get("/discover/next?after=old-a").json()["tournament"] is None
    monkeypatch.setattr(settings, "features", "discovery")  # the feature is off: not used even for a member
    assert client.get("/discover/next?after=old-a", headers=tokens["tunde"]).json()["tournament"] is None
