"""Progress Engine, second drop: streak freezes, tournament links, game opens, the backfill, public progress, retention."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import tournaments
from app.config import settings
from app.features.progress import service as progress
from app.features.progress.models import ActivityDay, GameOpen, Run
from app.models import Score, Tournament, TournamentEntry, TournamentGame
from .test_progress import clean_registry, env, on  # noqa: F401  (shared fixtures)

UTC = timezone.utc
D = date(2026, 10, 20)


def days(*offsets: int) -> list[date]:
    return [D + timedelta(days=o) for o in offsets]


def span(first: int, last: int) -> list[int]:
    return list(range(first, last + 1))


# --- streak freezes ----------------------------------------------------------------------------------------------

def test_seven_days_in_a_row_earn_one_freeze():
    assert progress.streak_state(days(*span(-6, 0)), D) == (7, 7, 1)


def test_fourteen_days_earn_a_second_freeze_and_no_more_than_two_are_kept():
    assert progress.streak_state(days(*span(-13, 0)), D) == (14, 14, 2)
    assert progress.streak_state(days(*span(-20, 0)), D) == (21, 21, 2)


def test_a_missed_day_spends_a_freeze_and_the_streak_carries_on():
    played = days(*span(-9, -3), -1, 0)  # seven days, one missed, two more
    assert progress.streak_state(played, D) == (9, 9, 0)


def test_the_frozen_day_itself_does_not_add_to_the_streak():
    played = days(*span(-8, -2), 0)  # seven days, a frozen day, today
    assert progress.streak_state(played, D)[0] == 8


def test_without_a_freeze_a_missed_day_still_breaks_the_streak():
    assert progress.streak_state(days(-5, -4, -3, -1, 0), D) == (2, 3, 0)


def test_two_missed_days_need_two_freezes():
    played = days(*span(-10, -4), -1, 0)  # seven days (one freeze), two missed, then back
    assert progress.streak_state(played, D) == (2, 7, 0)


def test_two_freezes_cover_two_missed_days():
    played = days(*span(-16, -3), 0)  # fourteen days (two freezes), two missed, then back
    current, best, left = progress.streak_state(played, D)
    assert (current, best, left) == (15, 15, 0)


def test_today_not_being_over_does_not_spend_a_freeze():
    assert progress.streak_state(days(*span(-7, -1)), D) == (7, 7, 1)


def test_a_freeze_is_not_spent_when_there_is_no_streak_to_save():
    assert progress.streak_state(days(-30), D) == (0, 1, 0)


def test_the_old_two_number_streaks_still_work():
    assert progress.streaks(days(-2, -1, 0), D) == (3, 3)


def test_the_snapshot_and_api_show_the_freezes_left(env, on, monkeypatch):
    Session, client, tokens, ids = env
    with Session() as db:
        for n in range(7):
            db.add(ActivityDay(user_id=ids["ada"], day=progress.local_day(progress.now()) - timedelta(days=n), runs=1, xp=12))
        db.commit()
        assert progress.snapshot(db, ids["ada"]).streak_freezes == 1
    assert client.get("/me/progress", headers=tokens["ada"]).json()["streak_freezes"] == 1


# --- tournament link ---------------------------------------------------------------------------------------------

def make_tournament(db, user_id, game="lanedash", enter=True):
    start = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
    t = Tournament(starts_at=start, ends_at=start + timedelta(days=1))
    db.add(t)
    db.flush()
    db.add(TournamentGame(tournament_id=t.id, game_name=game, position=1))
    if enter:
        db.add(TournamentEntry(tournament_id=t.id, user_id=user_id))
    db.commit()
    return t.id


def test_a_run_remembers_the_tournament_it_counted_for(env, on, monkeypatch):
    Session, client, tokens, ids = env
    monkeypatch.setattr(tournaments, "now", lambda: datetime(2026, 10, 20, 20, 0, tzinfo=UTC))
    with Session() as db:
        tid = make_tournament(db, ids["ada"])
    assert client.post("/games/lanedash/scores", json={"score": 10}, headers=tokens["ada"]).status_code == 200
    with Session() as db:
        assert db.scalar(select(Run.tournament_id)) == tid


def test_a_run_by_someone_who_did_not_enter_has_no_tournament(env, on, monkeypatch):
    Session, client, tokens, ids = env
    monkeypatch.setattr(tournaments, "now", lambda: datetime(2026, 10, 20, 20, 0, tzinfo=UTC))
    with Session() as db:
        make_tournament(db, ids["ada"], enter=False)
    client.post("/games/lanedash/scores", json={"score": 10}, headers=tokens["ada"])
    with Session() as db:
        assert db.scalar(select(Run.tournament_id)) is None


def test_a_run_in_a_game_outside_the_tournament_has_no_tournament(env, on, monkeypatch):
    Session, client, tokens, ids = env
    monkeypatch.setattr(tournaments, "now", lambda: datetime(2026, 10, 20, 20, 0, tzinfo=UTC))
    with Session() as db:
        make_tournament(db, ids["ada"], game="relic")
    client.post("/games/lanedash/scores", json={"score": 10}, headers=tokens["ada"])
    with Session() as db:
        assert db.scalar(select(Run.tournament_id)) is None


# --- game opens (C4) ---------------------------------------------------------------------------------------------

def test_the_first_open_is_recorded(env, monkeypatch):
    Session, _, _, ids = env
    when = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(progress, "now", lambda: when)
    with Session() as db:
        progress.record_open(db, ids["ada"], "lanedash")
        row = progress.game_open(db, ids["ada"], "lanedash")
    assert (row.count, row.first_at.replace(tzinfo=UTC), row.last_at.replace(tzinfo=UTC)) == (1, when, when)


def test_opening_again_counts_and_moves_the_last_time_only(env, monkeypatch):
    Session, _, _, ids = env
    first, later = datetime(2026, 10, 20, 12, 0, tzinfo=UTC), datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
    with Session() as db:
        monkeypatch.setattr(progress, "now", lambda: first)
        progress.record_open(db, ids["ada"], "lanedash")
        monkeypatch.setattr(progress, "now", lambda: later)
        progress.record_open(db, ids["ada"], "lanedash")
        row = progress.game_open(db, ids["ada"], "lanedash")
    assert row.count == 2
    assert row.first_at.replace(tzinfo=UTC) == first and row.last_at.replace(tzinfo=UTC) == later


def test_opens_are_kept_per_player_and_per_game(env):
    Session, _, _, ids = env
    with Session() as db:
        progress.record_open(db, ids["ada"], "lanedash")
        progress.record_open(db, ids["ada"], "relic")
        progress.record_open(db, ids["tunde"], "lanedash")
        assert db.scalar(select(func.count()).select_from(GameOpen)) == 3
        assert progress.game_open(db, ids["tunde"], "relic") is None


def test_a_signed_in_play_records_an_open(env, on):
    Session, client, tokens, ids = env
    assert client.post("/games/lanedash/plays", headers=tokens["ada"]).status_code == 204
    with Session() as db:
        assert progress.game_open(db, ids["ada"], "lanedash").count == 1


def test_an_anonymous_play_records_no_open_but_still_counts_as_a_play(env, on):
    Session, client, _, _ = env
    assert client.post("/games/lanedash/plays").status_code == 204
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(GameOpen)) == 0


def test_no_open_is_recorded_while_the_feature_is_off(env):
    Session, client, tokens, _ = env
    assert client.post("/games/lanedash/plays", headers=tokens["ada"]).status_code == 204
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(GameOpen)) == 0


# --- backfill from scores ----------------------------------------------------------------------------------------

def test_the_backfill_gives_existing_players_a_run_per_game_they_scored_in(env):
    Session, _, _, ids = env
    saved = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    with Session() as db:
        db.add_all([
            Score(user_id=ids["ada"], game_name="lanedash", best=300, stats={"score": 300}, updated_at=saved),
            Score(user_id=ids["ada"], game_name="relic", best=40, stats=None, updated_at=saved),
        ])
        db.commit()
        assert progress.backfill_from_scores(db) == 2
        snap = progress.snapshot(db, ids["ada"])
        runs = db.scalars(select(Run).order_by(Run.game_name)).all()
    assert (snap.runs, snap.distinct_games) == (2, 2)
    assert [(r.game_name, r.score, r.stats) for r in runs] == [("lanedash", 300, {"score": 300}), ("relic", 40, {"score": 40})]
    assert runs[0].created_at.replace(tzinfo=UTC) == saved


def test_the_backfill_can_run_twice_without_doubling_anything(env):
    Session, _, _, ids = env
    with Session() as db:
        db.add(Score(user_id=ids["ada"], game_name="lanedash", best=5, stats={"score": 5}))
        db.commit()
        assert progress.backfill_from_scores(db) == 1
        assert progress.backfill_from_scores(db) == 0
        assert db.scalar(select(func.count()).select_from(Run)) == 1


def test_the_backfill_skips_games_a_player_already_has_runs_in(env):
    Session, _, _, ids = env
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 9})
        db.add(Score(user_id=ids["ada"], game_name="lanedash", best=9, stats={"score": 9}))
        db.commit()
        assert progress.backfill_from_scores(db) == 0


def test_the_backfill_gives_no_streak_and_no_xp(env):
    Session, _, _, ids = env
    with Session() as db:
        db.add(Score(user_id=ids["ada"], game_name="lanedash", best=5, stats={"score": 5}))
        db.commit()
        progress.backfill_from_scores(db)
        snap = progress.snapshot(db, ids["ada"])
        assert (snap.current_streak, snap.best_streak, snap.xp) == (0, 0, 0)
        assert db.scalar(select(func.count()).select_from(ActivityDay)) == 0


# --- public progress ---------------------------------------------------------------------------------------------

def test_anyone_can_see_a_players_public_progress(env, on):
    Session, client, tokens, ids = env
    client.post("/games/lanedash/scores", json={"score": 10}, headers=tokens["ada"])
    res = client.get(f"/players/{ids['ada']}/progress")
    assert res.status_code == 200
    assert res.json() == {"runs": 1, "distinct_games": 1, "current_streak": 1, "best_streak": 1, "level": 1, "tournament_wins": 0}


def test_public_progress_never_shows_xp_or_an_email(env, on):
    _, client, _, ids = env
    text = client.get(f"/players/{ids['ada']}/progress").text
    assert "xp" not in text and "@" not in text and "email" not in text


def test_public_progress_of_an_unknown_player_is_404(env, on):
    _, client, _, _ = env
    assert client.get("/players/99999/progress").status_code == 404


def test_public_progress_is_404_while_the_feature_is_off(env):
    _, client, _, ids = env
    assert client.get(f"/players/{ids['ada']}/progress").status_code == 404


def test_a_private_profile_hides_progress_through_the_visibility_hook(env, on):
    _, client, _, ids = env
    progress.register_visibility("profiles", lambda db, user_id: user_id != ids["ada"])
    assert client.get(f"/players/{ids['ada']}/progress").status_code == 404
    assert client.get(f"/players/{ids['tunde']}/progress").status_code == 200


# --- keeping runs for 180 days -----------------------------------------------------------------------------------

def test_runs_older_than_the_retention_period_are_removed(env, monkeypatch):
    Session, _, _, ids = env
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(progress, "now", lambda: now)
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        old, edge = (Run(user_id=ids["ada"], game_name="lanedash", score=1, stats={"score": 1}, created_at=now - timedelta(days=d)) for d in (181, 179))
        db.add_all([old, edge])
        db.commit()
        assert progress.purge_old_runs(db) == 1
        assert db.scalar(select(func.count()).select_from(Run)) == 2


def test_purging_leaves_daily_activity_and_awards_alone(env, monkeypatch):
    Session, _, _, ids = env
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(progress, "now", lambda: now)
    progress.register("profiles", [progress.Rule("badge", "first", lambda s, r: True)])
    with Session() as db:
        progress.after_run(db, ids["ada"], "lanedash", {"score": 1})
        db.query(Run).update({Run.created_at: now - timedelta(days=400)})
        db.commit()
        progress.purge_old_runs(db)
        assert db.scalar(select(func.count()).select_from(Run)) == 0
        assert db.scalar(select(func.count()).select_from(ActivityDay)) == 1
        assert progress.awards_of(db, ids["ada"])


def test_the_retention_period_is_a_setting(env, monkeypatch):
    Session, _, _, ids = env
    monkeypatch.setattr(settings, "runs_retention_days", 30)
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(progress, "now", lambda: now)
    with Session() as db:
        db.add(Run(user_id=ids["ada"], game_name="lanedash", score=1, stats={"score": 1}, created_at=now - timedelta(days=31)))
        db.commit()
        assert progress.purge_old_runs(db) == 1


def test_the_purge_runs_at_most_once_an_hour(env, monkeypatch):
    Session, _, _, ids = env
    now = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]
    monkeypatch.setattr(progress, "now", lambda: now[0])
    monkeypatch.setattr(progress, "_last_purge", None)
    with Session() as db:
        db.add(Run(user_id=ids["ada"], game_name="lanedash", score=1, stats={"score": 1}, created_at=now[0] - timedelta(days=200)))
        db.commit()
        assert progress.maybe_purge(db) == 1  # first call purges
        db.add(Run(user_id=ids["ada"], game_name="lanedash", score=1, stats={"score": 1}, created_at=now[0] - timedelta(days=200)))
        db.commit()
        now[0] += timedelta(minutes=30)
        assert progress.maybe_purge(db) == 0  # too soon
        now[0] += timedelta(minutes=31)
        assert progress.maybe_purge(db) == 1  # an hour has passed


# --- running the backfill from the command line ------------------------------------------------------------------

def test_the_backfill_command_reports_how_many_runs_it_made(env, monkeypatch, capsys):
    from app.features.progress import backfill
    Session, _, _, ids = env
    with Session() as db:
        db.add(Score(user_id=ids["ada"], game_name="lanedash", best=5, stats={"score": 5}))
        db.commit()
    monkeypatch.setattr(backfill, "SessionLocal", Session)
    assert backfill.main() == 0
    assert "Made 1 run" in capsys.readouterr().out
    assert backfill.main() == 0
    assert "Made 0 runs" in capsys.readouterr().out
