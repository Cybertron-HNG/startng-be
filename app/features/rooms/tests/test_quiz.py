import pytest

from app.features.rooms import core
from app.features.rooms.games import quiz
from app.models import Game
from app.routers.live import get_sessionmaker
from app.main import app

from .test_rooms import env, url  # noqa: F401  (the fixture and the address helper)

SETUP = {"title": "Capitals", "questions": [
    {"q": "Capital of Nigeria?", "answers": ["Lagos", "Abuja", "Kano"], "correct": 1, "seconds": 20},
    {"q": "Capital of Ghana?", "answers": ["Accra", "Kumasi"], "correct": 0, "seconds": 10},
]}


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000]
    monkeypatch.setattr(quiz, "now_ms", lambda: now[0])
    return now


def started(players=3):
    options, error = quiz.validate_setup(SETUP)
    assert error is None
    return quiz.new_state(players, options)


def test_setup_is_checked():
    assert quiz.validate_setup(None)[1]
    assert quiz.validate_setup({"questions": []})[1] == "Add at least one question."
    assert "2 to 4" in quiz.validate_setup({"questions": [{"q": "a", "answers": ["x"], "correct": 0}]})[1]
    assert "right" in quiz.validate_setup({"questions": [{"q": "a", "answers": ["x", "y"], "correct": 2}]})[1]
    assert "5 to 120" in quiz.validate_setup({"questions": [{"q": "a", "answers": ["x", "y"], "correct": 0, "seconds": 1}]})[1]
    assert quiz.validate_setup({"questions": [{"q": "a", "answers": ["x", "y"], "correct": True}]})[1]
    too_many = {"questions": [{"q": "a", "answers": ["x", "y"], "correct": 0}] * (quiz.MAX_QUESTIONS + 1)}
    assert "up to" in quiz.validate_setup(too_many)[1]
    options, error = quiz.validate_setup({"questions": [{"q": "  a  ", "answers": ["x", "y"], "correct": 0}]})
    assert error is None and options["title"] == "Quiz" and options["questions"][0]["q"] == "a" and options["questions"][0]["seconds"] == 20
    with pytest.raises(ValueError):
        quiz.new_state(3, None)


def test_a_countdown_comes_first_and_answers_wait_for_it(clock):
    s = started()
    assert s["phase"] == "ready"
    assert quiz.apply(s, 1, {"answer": 1})[1] == "That question is closed."
    clock[0] += quiz.READY_MS
    s, changed = quiz.tick(s, clock[0])
    assert changed and s["phase"] == "question"
    assert quiz.apply(s, 1, {"answer": 1})[1] is None


def test_faster_right_answers_score_more_and_wrong_ones_nothing(clock):
    s = started(4)
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    quiz.apply(s, 1, {"answer": 1})  # at once
    clock[0] += 10_000
    quiz.apply(s, 2, {"answer": 1})  # half the time gone
    quiz.apply(s, 3, {"answer": 0})  # wrong
    clock[0] += 10_000
    s, _ = quiz.tick(s, clock[0])
    assert s["phase"] == "reveal"
    assert s["scores"] == {1: 1000, 2: 750, 3: 0}
    assert s["counts"] == [1, 2, 0]


def test_nobody_sees_the_right_answer_before_the_question_closes(clock):
    s = started()
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    for seat in (0, 1, 2):
        assert "correct" not in quiz.view(s, seat)
    quiz.apply(s, 1, {"answer": 0})
    assert quiz.view(s, 1)["choice"] == 0 and "choice" not in quiz.view(s, 2)


def test_a_question_closes_when_everyone_answered(clock):
    s = started(3)
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    quiz.apply(s, 1, {"answer": 1})
    assert quiz.tick(s, clock[0])[1] is False
    quiz.apply(s, 2, {"answer": 0})
    s, changed = quiz.tick(s, clock[0])
    assert changed and s["phase"] == "reveal" and quiz.view(s, 1)["correct"] == 1


def test_one_answer_each_and_only_valid_ones(clock):
    s = started()
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    assert quiz.apply(s, 1, {"answer": 5})[1].startswith("Pick an answer")
    assert quiz.apply(s, 1, {"answer": "1"})[1].startswith("Pick an answer")
    assert quiz.apply(s, 1, {"answer": 1})[1] is None
    assert quiz.apply(s, 1, {"answer": 0})[1] == "You already answered."
    assert quiz.apply(s, 9, {"answer": 0})[1] == "You are not playing."


def test_only_the_host_can_skip_and_it_moves_things_on(clock):
    s = started()
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    assert quiz.apply(s, 1, {"skip": True})[1] == "Pick an answer from 0 to 2."
    quiz.apply(s, 0, {"skip": True})
    assert s["phase"] == "reveal"
    quiz.apply(s, 0, {"skip": True})
    assert s["phase"] == "question" and s["index"] == 1
    assert quiz.apply(s, 0, {"answer": 0})[1] == "The host moves things on with skip."


def test_reveal_moves_on_by_itself_then_ends_with_standings(clock):
    s = started(3)
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    quiz.apply(s, 1, {"answer": 1})
    clock[0] += 20_000
    quiz.tick(s, clock[0])
    assert s["phase"] == "reveal" and quiz.view(s, 1)["points"] == 1000 and quiz.view(s, 2)["points"] == 0
    clock[0] += quiz.REVEAL_MS
    quiz.tick(s, clock[0])
    assert s["phase"] == "question" and s["index"] == 1
    quiz.apply(s, 2, {"answer": 0})
    clock[0] += 10_000
    quiz.tick(s, clock[0])
    clock[0] += quiz.REVEAL_MS
    quiz.tick(s, clock[0])
    assert s["phase"] == "over"
    result = quiz.result(s)
    assert result["winner"] in (1, 2) and result["reason"] == "finished" and len(result["standings"]) == 2
    assert quiz.apply(s, 1, {"answer": 0})[1] == "The quiz is over."
    assert quiz.view(s, 1)["standings"] == result["standings"]


def test_someone_who_joins_late_can_play(clock):
    s = started(2)
    clock[0] += quiz.READY_MS
    quiz.tick(s, clock[0])
    assert quiz.apply(s, 2, {"answer": 1})[1] == "You are not playing."
    quiz.add_player(s, 2)
    assert quiz.apply(s, 2, {"answer": 1})[1] is None


# ---- through the rooms server

@pytest.fixture
def live(env):  # noqa: F811
    client, ids, now = env
    Session = app.dependency_overrides[get_sessionmaker]()
    with Session() as db:
        db.add(Game(name="quiz", owner_id=ids["ada"], repo_url="https://github.com/ada/quiz"))
        db.commit()
    return client, ids, now


def test_a_host_sets_up_starts_and_players_answer(live, clock):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], game="quiz")) as host:
        welcome = host.receive_json()
        assert welcome["type"] == "welcome" and welcome["host"] == 0 and welcome["you"] == 0
        code = welcome["room"]
        host.send_json({"type": "start"})
        assert host.receive_json()["code"] == "need_players"
        host.send_json({"type": "setup", "data": {"questions": []}})
        assert host.receive_json()["code"] == "bad_setup"
        host.send_json({"type": "setup", "data": SETUP})
        assert host.receive_json()["type"] == "setup_ok"
        with client.websocket_connect(url(ids["tunde"], room=code, game="quiz")) as player:
            assert player.receive_json()["you"] == 1
            assert host.receive_json()["type"] == "join"
            host.send_json({"type": "start"})
            started_msg = player.receive_json()
            assert started_msg["type"] == "welcome" and started_msg["status"] == "playing" and "now" in started_msg
            assert started_msg["state"]["phase"] == "ready" and "correct" not in started_msg["state"]
            host.receive_json()  # the host's own welcome
            player.send_json({"type": "move", "data": {"answer": 1}})
            assert player.receive_json()["code"] == "bad_move"  # still in the countdown


def test_a_player_cannot_set_up_or_start_and_nobody_can_match_or_play_a_bot(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], game="quiz")) as host:
        code = host.receive_json()["room"]
        with client.websocket_connect(url(ids["tunde"], room=code, game="quiz")) as player:
            player.receive_json()
            host.receive_json()
            player.send_json({"type": "setup", "data": SETUP})
            player.send_json({"type": "start"})
            host.send_json({"type": "start"})  # no setup yet, so the start fails for a different reason
            assert host.receive_json()["code"] == "bad_setup"
            player.send_json({"type": "ping"})
            assert player.receive_json()["type"] == "pong"  # the player's setup and start were ignored, not answered
            host.send_json({"type": "bot"})
            host.send_json({"type": "ping"})
            assert host.receive_json()["type"] == "pong"  # no bot was added
    with client.websocket_connect(url(ids["chi"], room="match", game="quiz")) as chi:
        assert chi.receive_json()["code"] == "no_match"


def test_a_big_setup_is_allowed_but_a_big_move_is_not(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], game="quiz")) as host:
        host.receive_json()
        big = {"title": "Big", "questions": [{"q": "q" * 190, "answers": ["a" * 90, "b" * 90], "correct": 0}] * 40}
        host.send_json({"type": "setup", "data": big})
        assert host.receive_json()["type"] == "setup_ok"
        host.send_json({"type": "move", "data": {"answer": 0, "pad": "x" * (core.MAX_MESSAGE + 1)}})
        assert host.receive_json()["code"] == "too_big"
