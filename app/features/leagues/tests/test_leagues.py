"""Leagues (PRD M2). The PRD's "done when": a 300-player simulation in each track, deterministic ties, settling twice
does nothing, the tracks never affect each other, and a 2-hour round across midnight settles correctly."""
import math
import random
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events
from app.config import settings
from app.db import Base, get_db
from app.features.leagues import service
from app.features.leagues.models import LeagueEntry, LeagueMember, LeagueRound
from app.features.progress.models import Run
from app.main import app
from app.models import Game, User
from app.security import create_token

LOWEST = [{"key": "strokes", "label": "Strokes", "order": "lowest", "format": "number"}]


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
    monkeypatch.setattr(service, "SessionLocal", Session)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        owner = User(email="owner@example.com", name="Owner")
        db.add(owner)
        db.flush()
        db.add_all([Game(name="tap", owner_id=owner.id, repo_url="x"), Game(name="dash", owner_id=owner.id, repo_url="y"),
                    Game(name="golf", owner_id=owner.id, repo_url="z", leaderboard=LOWEST)])
        db.commit()
    yield Session
    app.dependency_overrides.clear()


@pytest.fixture
def published(monkeypatch):
    seen = []

    def grab(name):
        def handler(payload):
            seen.append((name, payload))
        return handler

    handlers = {name: grab(name) for name in ("league_round_finished", "league_promoted")}
    for name, fn in handlers.items():
        events.subscribe(name)(fn)
    yield seen
    for name, fn in handlers.items():
        events.unsubscribe(name, fn)


def start_of(track: str, at: datetime) -> datetime:
    return service.window(track, at)[0]


# A moment where a Sprint, a Daily and a Weekly round all start together.
T0 = start_of("weekly", datetime(2026, 10, 19, 12, tzinfo=timezone.utc))


def users(Session, n: int) -> list[int]:
    with Session() as db:
        start = db.query(User).count()
        made = [User(email=f"p{start + i}@example.com", name=f"P{start + i}") for i in range(n)]
        db.add_all(made)
        db.commit()
        return [u.id for u in made]


def run(Session, user_id: int, game: str, value: int, at: datetime, flagged: bool = False, join: bool = True) -> int:
    key = "strokes" if game == "golf" else "score"
    with Session() as db:
        r = Run(user_id=user_id, game_name=game, score=value, stats={key: value}, created_at=at, flagged=flagged)
        db.add(r)
        db.commit()
        if join and not flagged:
            service.join(db, user_id, at=at)
        return r.id


def set_tier(Session, user_id: int, track: str, tier: int, last_round_start: datetime | None = None) -> None:
    with Session() as db:
        db.add(LeagueMember(user_id=user_id, track=track, tier=tier, last_round_start=last_round_start))
        db.commit()


def settle_track(Session, track: str, at: datetime) -> None:
    with Session() as db:
        service.settle_due(db, track, at)


def entries(Session, track: str, starts_at: datetime) -> dict[int, LeagueEntry]:
    with Session() as db:
        rnd = db.scalar(select(LeagueRound).where(LeagueRound.track == track, LeagueRound.starts_at == starts_at))
        return {e.user_id: e for e in db.scalars(select(LeagueEntry).where(LeagueEntry.round_id == rnd.id))}


def tiers(Session, track: str) -> dict[int, int]:
    with Session() as db:
        return {m.user_id: m.tier for m in db.scalars(select(LeagueMember).where(LeagueMember.track == track))}


# ---- the 300-player simulation


def test_300_players_promote_and_relegate_the_right_people_in_each_track(env):
    Session = env
    rng = random.Random(42)
    ids = users(Session, 300)
    start_tiers = {}
    for track in service.TRACKS:  # a different shuffle of 75 players per tier in every track
        spread = [t for t in range(4) for _ in range(75)]
        rng.shuffle(spread)
        start_tiers[track] = dict(zip(ids, spread))
        for uid in ids:
            set_tier(Session, uid, track, start_tiers[track][uid])
    games = ["tap", "dash", "golf"]
    plays = {}  # uid -> (game, value, at)
    values = rng.sample(range(1, 1_000_000), 300)
    for i, uid in enumerate(ids):
        plays[uid] = (games[i % 3], values[i], T0 + timedelta(seconds=10 * i))
        run(Session, uid, *plays[uid])

    # What each player should score, worked out independently: one game each, no equal values.
    expected_points = {}
    for game in games:
        played = [uid for uid in ids if plays[uid][0] == game]
        for uid in played:
            mine = plays[uid][1]
            beaten = sum((plays[o][1] > mine) if game == "golf" else (plays[o][1] < mine) for o in played)
            expected_points[uid] = math.floor(Fraction(100 * beaten, len(played) - 1) + Fraction(1, 2))

    for track in service.TRACKS:
        settle_track(Session, track, T0 + service.length(track))
        got = entries(Session, track, T0)
        assert len(got) == 300
        after = tiers(Session, track)
        for tier in range(4):
            arrived = [uid for uid in ids if start_tiers[track][uid] == tier]  # in the order they first played
            for g in range(0, len(arrived), service.GROUP_SIZE):
                group = arrived[g:g + service.GROUP_SIZE]
                assert len({got[u].group_id for u in group}) == 1  # filled in arrival order
                ranked = sorted(group, key=lambda u: (-expected_points[u], plays[u][2], u))
                for place, uid in enumerate(ranked, start=1):
                    e = got[uid]
                    assert (e.place, e.points) == (place, expected_points[uid])
                    if place <= 5 and tier < 3:
                        want = "up"
                    elif place > len(group) - 5 and tier > 0:
                        want = "down"
                    else:
                        want = "stay"
                    assert e.result == want, (track, tier, place)
                    assert after[uid] == tier + {"up": 1, "down": -1, "stay": 0}[want]


# ---- points


def test_lowest_wins_games_and_the_best_five_games(env):
    Session = env
    a, b, c = users(Session, 3)
    run(Session, a, "golf", 70, T0 + timedelta(minutes=1))  # fewest strokes: beats both
    run(Session, b, "golf", 80, T0 + timedelta(minutes=2))
    run(Session, c, "golf", 90, T0 + timedelta(minutes=3))
    with Session() as db:
        rnd = service.round_at(db, "daily", T0)
        sc = service.scores(db, rnd)
    assert (sc[a].points, sc[b].points, sc[c].points) == (100, 50, 0)


def test_only_the_best_five_games_count_and_a_lone_player_gets_100(env):
    Session = env
    [a] = users(Session, 1)
    with Session() as db:
        db.add_all([Game(name=f"g{i}", owner_id=a, repo_url=f"r{i}") for i in range(6)])
        db.commit()
    for i in range(6):
        run(Session, a, f"g{i}", 5, T0 + timedelta(minutes=i))
    with Session() as db:
        sc = service.scores(db, service.round_at(db, "daily", T0))[a]
    assert (sc.points, sc.games) == (500, 6)


def test_the_best_run_counts_and_ties_split_the_difference(env):
    Session = env
    a, b, c = users(Session, 3)
    run(Session, a, "tap", 10, T0 + timedelta(minutes=1))
    run(Session, a, "tap", 50, T0 + timedelta(minutes=2))  # a's best
    run(Session, b, "tap", 50, T0 + timedelta(minutes=3))  # tied with a
    run(Session, c, "tap", 20, T0 + timedelta(minutes=4))
    with Session() as db:
        sc = service.scores(db, service.round_at(db, "daily", T0))
    assert (sc[a].points, sc[b].points, sc[c].points) == (75, 75, 0)


def test_flagged_runs_never_count_or_seat_anyone(env):
    Session = env
    a, b = users(Session, 2)
    run(Session, a, "tap", 999, T0 + timedelta(minutes=1), flagged=True)
    run(Session, b, "tap", 1, T0 + timedelta(minutes=2))
    assert set(entries(Session, "daily", T0)) == {b}
    with Session() as db:
        assert service.scores(db, service.round_at(db, "daily", T0))[b].points == 100


# ---- ties


def test_ties_are_always_settled_in_the_same_order(env):
    Session = env
    ids = users(Session, 12)
    same = T0 + timedelta(minutes=5)
    for uid in ids:
        run(Session, uid, "tap", 7, same)  # identical score at the same moment: user id decides
    settle_track(Session, "daily", T0 + timedelta(days=1))
    got = entries(Session, "daily", T0)
    assert [got[u].place for u in ids] == list(range(1, 13))
    assert all(got[u].points == 50 for u in ids)
    # Earlier wins over user id when points are equal.
    late, early = users(Session, 2)
    nxt = T0 + timedelta(days=1)
    for uid in ids[:10]:
        run(Session, uid, "dash", 3, nxt + timedelta(minutes=1))
    run(Session, late, "dash", 9, nxt + timedelta(minutes=30))
    run(Session, early, "dash", 9, nxt + timedelta(minutes=10))
    with Session() as db:
        t = service.standings(db, "daily", early, at=nxt + timedelta(hours=1))
    assert [r.user.id for r in t.rows[:2]] == [early, late]


# ---- settling


def test_settling_twice_does_nothing(env, published):
    Session = env
    ids = users(Session, 12)
    for i, uid in enumerate(ids):
        run(Session, uid, "tap", i, T0 + timedelta(minutes=i))
    end = T0 + timedelta(days=1)
    with Session() as db:
        stale = db.scalar(select(LeagueRound).where(LeagueRound.track == "daily"))
        db.expunge(stale)  # another request read the round before it was settled
    settle_track(Session, "daily", end)
    first = {u: (e.place, e.points, e.result) for u, e in entries(Session, "daily", T0).items()}
    tiers_once = tiers(Session, "daily")
    settle_track(Session, "daily", end + timedelta(hours=1))
    with Session() as db:
        stale.settled_at = None  # it still thinks the round is open
        db.add(stale)
        service.settle(db, stale, end + timedelta(hours=2))
    assert {u: (e.place, e.points, e.result) for u, e in entries(Session, "daily", T0).items()} == first
    assert tiers(Session, "daily") == tiers_once
    assert sum(name == "league_round_finished" and p["track"] == "daily" for name, p in published) == 1


def test_a_round_is_settled_only_after_it_ends(env):
    Session = env
    [a] = users(Session, 1)
    run(Session, a, "tap", 1, T0 + timedelta(minutes=1))
    settle_track(Session, "daily", T0 + timedelta(hours=23))
    assert entries(Session, "daily", T0)[a].result is None


def test_small_groups_merge_with_the_tier_below_and_tiny_tables_wait(env):
    Session = env
    silver, bronze = users(Session, 6), users(Session, 6)
    for uid in silver:
        set_tier(Session, uid, "daily", 1)
    for i, uid in enumerate(bronze + silver):  # Silver scores highest
        run(Session, uid, "tap", i, T0 + timedelta(minutes=i))
    settle_track(Session, "daily", T0 + timedelta(days=1))
    got = entries(Session, "daily", T0)
    # 12 players in one table: the 5 best (Silver) go up, the 5 worst go down where they can.
    best = sorted(silver + bronze, key=lambda u: -got[u].points)
    assert [got[u].result for u in best[:5]] == ["up"] * 5
    assert {got[u].result for u in best[-5:]} == {"stay"}  # the bottom 5 are Bronze: nowhere to go
    assert tiers(Session, "daily")[best[0]] == 2

    nxt = T0 + timedelta(days=1)
    few = users(Session, 6)
    for i, uid in enumerate(few):
        run(Session, uid, "tap", i, nxt + timedelta(minutes=i))
    with Session() as db:
        t = service.standings(db, "daily", few[0], at=nxt + timedelta(hours=1))
        assert t.waiting and t.points_to_promotion is None
    settle_track(Session, "daily", nxt + timedelta(days=1))
    got = entries(Session, "daily", nxt)
    assert {got[u].result for u in few} == {"stay"}  # waiting for players: nobody moves


def test_a_player_whose_runs_were_all_flagged_later_is_out(env):
    Session = env
    ids = users(Session, 11)
    run_ids = [run(Session, uid, "tap", i, T0 + timedelta(minutes=i)) for i, uid in enumerate(ids)]
    with Session() as db:
        db.get(Run, run_ids[0]).flagged = True  # held for review after it seated them
        db.commit()
    settle_track(Session, "daily", T0 + timedelta(days=1))
    got = entries(Session, "daily", T0)
    assert (got[ids[0]].result, got[ids[0]].place) == ("out", None)
    assert sorted(got[u].place for u in ids[1:]) == list(range(1, 11))


def test_missing_two_rounds_drops_a_tier_on_return(env):
    Session = env
    a, b = users(Session, 2)
    day = service.length("daily")
    set_tier(Session, a, "daily", 2, last_round_start=T0 - 3 * day)  # missed 2 rounds
    set_tier(Session, b, "daily", 2, last_round_start=T0 - 2 * day)  # missed 1
    run(Session, a, "tap", 1, T0 + timedelta(minutes=1))
    run(Session, b, "tap", 1, T0 + timedelta(minutes=2))
    got = entries(Session, "daily", T0)
    assert (got[a].tier, got[b].tier) == (1, 2)


# ---- tracks


def test_the_tracks_never_affect_each_other(env, published):
    Session = env
    a, b = users(Session, 2)
    run(Session, a, "tap", 50, T0 + timedelta(minutes=10))  # first Sprint round
    run(Session, b, "tap", 10, T0 + timedelta(hours=3))  # third Sprint round, same Daily and Weekly round
    with Session() as db:
        first_sprint = service.scores(db, service.round_at(db, "sprint", T0))
        daily = service.scores(db, service.round_at(db, "daily", T0))
    assert set(first_sprint) == {a} and first_sprint[a].points == 100
    assert (daily[a].points, daily[b].points) == (100, 0)  # the Daily round sees both runs
    settle_track(Session, "sprint", T0 + timedelta(days=1))
    with Session() as db:
        assert db.scalar(select(LeagueRound).where(LeagueRound.track == "daily")).settled_at is None
    assert entries(Session, "daily", T0)[a].result is None
    assert published == []  # Sprint sends no events


def test_daily_and_weekly_send_round_and_promotion_events(env, published):
    Session = env
    ids = users(Session, 10)
    for i, uid in enumerate(ids):
        run(Session, uid, "tap", i, T0 + timedelta(minutes=i))
    settle_track(Session, "daily", T0 + timedelta(days=1))
    settle_track(Session, "weekly", T0 + timedelta(days=7))
    for track in ("daily", "weekly"):
        assert [p["track"] for n, p in published if n == "league_round_finished"].count(track) == 1
        promoted = {p["user_id"] for n, p in published if n == "league_promoted" and p["track"] == track}
        assert promoted == set(ids[-5:])
        assert all(p["tier"] == "silver" for n, p in published if n == "league_promoted")


def test_a_two_hour_round_across_midnight_settles_correctly(env):
    Session = env
    # Sprint blocks line up with the reset hour (18:00 Nigeria time, 17:00 UTC), so one runs 23:00 to 01:00 UTC.
    start = datetime(2026, 10, 21, 23, tzinfo=timezone.utc)
    assert service.window("sprint", start + timedelta(minutes=90)) == (start, start + timedelta(hours=2))
    ids = users(Session, 12)
    for i, uid in enumerate(ids):  # half before midnight, half after
        run(Session, uid, "tap", i, start + timedelta(minutes=10 * i))
    late = users(Session, 1)[0]
    run(Session, late, "tap", 999, start + timedelta(hours=2, minutes=5))  # the next round
    settle_track(Session, "sprint", start + timedelta(hours=2, minutes=6))
    got = entries(Session, "sprint", start)
    assert set(got) == set(ids)
    assert [got[u].place for u in reversed(ids)] == list(range(1, 13))
    assert [got[u].result for u in ids[-5:]] == ["up"] * 5
    assert entries(Session, "sprint", start + timedelta(hours=2))[late].result is None


# ---- joining through runs


def test_runs_seat_players_through_the_event_but_old_cleared_runs_do_not(env):
    Session = env
    a, b = users(Session, 2)
    now = service.now()
    rid = run(Session, a, "tap", 5, now, join=False)
    service._on_run({"user_id": a, "game": "tap", "run_id": rid})
    with Session() as db:
        for track in service.TRACKS:
            assert service.standings(db, track, a).group_number == 1
    old = run(Session, b, "tap", 5, now - timedelta(days=8), join=False)  # cleared by an admin long after
    service._on_run({"user_id": b, "game": "tap", "run_id": old})
    with Session() as db:
        assert all(service.standings(db, t, b).group_number is None for t in service.TRACKS)


def test_a_full_group_starts_another(env):
    Session = env
    ids = users(Session, 31)
    for i, uid in enumerate(ids):
        run(Session, uid, "tap", i, T0 + timedelta(seconds=i))
    got = entries(Session, "daily", T0)
    assert len({got[u].group_id for u in ids[:30]}) == 1 and got[ids[30]].group_id != got[ids[0]].group_id
    assert sorted(got[u].seat for u in ids[:30]) == list(range(1, 31))


def test_a_seat_left_by_a_deleted_account_is_given_out_again(env):
    Session = env
    ids, new = users(Session, 5), users(Session, 2)
    for i, uid in enumerate(ids):
        run(Session, uid, "tap", i, T0 + timedelta(seconds=i))
    gone = entries(Session, "daily", T0)[ids[1]]
    with Session() as db:  # deleting an account removes its entries (ON DELETE CASCADE; SQLite here doesn't cascade)
        db.query(LeagueEntry).filter(LeagueEntry.user_id == ids[1]).delete()
        db.query(User).filter(User.id == ids[1]).delete()
        db.commit()
    for i, uid in enumerate(new):  # before the fix, the first of these clashed with seat 5 and was never seated
        run(Session, uid, "tap", 10 + i, T0 + timedelta(minutes=1, seconds=i))
    got = entries(Session, "daily", T0)
    assert got[new[0]].group_id == gone.group_id and got[new[0]].seat == gone.seat
    assert got[new[1]].seat == 6
    assert sorted(e.seat for e in got.values()) == [1, 2, 3, 4, 5, 6]


# ---- the API


def test_the_api(env, monkeypatch):
    Session = env
    fixed = T0 + timedelta(hours=1)
    monkeypatch.setattr(service, "now", lambda: fixed)
    client = TestClient(app)
    ids = users(Session, 12)
    headers = {"Authorization": f"Bearer {create_token(ids[0])}"}
    monkeypatch.setattr(settings, "features", "")
    assert client.get("/leagues", headers=headers).status_code == 404  # ships dark
    monkeypatch.setattr(settings, "features", "*")
    assert client.get("/leagues").status_code == 401
    assert client.get("/leagues/monthly", headers=headers).status_code == 404

    res = client.get("/leagues", headers=headers).json()
    assert [t["track"] for t in res["tracks"]] == ["sprint", "daily", "weekly"]
    assert res["tracks"][1]["group"] is None and res["tracks"][1]["tier"] == "bronze"

    now = fixed
    for i, uid in enumerate(ids):
        run(Session, uid, "tap", i, now - timedelta(seconds=30 - i))
    t = client.get("/leagues/daily", headers=headers).json()
    assert t["group"] == 1 and not t["waiting"] and len(t["rows"]) == 12
    assert t["me"]["zone"] == "stay" and t["me"]["place"] == 12 and t["me"]["me"]  # last, but Bronze can't go down
    assert [r["zone"] for r in t["rows"][:5]] == ["up"] * 5
    assert t["points_to_promotion"] == t["rows"][4]["points"] - t["me"]["points"] + 1
    assert "email" not in t["rows"][0]["player"]
    assert len(client.get("/leagues", headers=headers).json()["tracks"][1]["top"]) == 3

    with Session() as db:
        service.settle_due(db, "daily", now + timedelta(days=1))
    h = client.get("/leagues/daily/history", headers=headers).json()
    assert h["rounds"][0]["result"] == "stay" and h["rounds"][0]["place"] == 12
