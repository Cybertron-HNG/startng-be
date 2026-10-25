import pytest

from app.features.rooms.games import bingo
from app.main import app
from app.models import Game
from app.routers.live import get_sessionmaker

from .test_rooms import env, url  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    now = [5_000_000]
    monkeypatch.setattr(bingo, "now_ms", lambda: now[0])
    return now


def numbers(players=3, **extra):
    options, error = bingo.validate_setup({"mode": "numbers", **extra})
    assert error is None, error
    return bingo.new_state(players, options)


def call_on_card(state, seat, squares):
    """Make the items on those squares of a card count as called."""
    for sq in squares:
        item = state["cards"][seat][sq]
        if item not in state["called"]:
            state["called"].append(item)


def test_setup_is_checked():
    assert bingo.validate_setup(None)[1]
    assert bingo.validate_setup({"mode": "x"})[1]
    assert "3 to 60" in bingo.validate_setup({"speed": 1})[1]
    assert bingo.validate_setup({"pattern": "x"})[1]
    assert "24 to 100" in bingo.validate_setup({"mode": "terms", "terms": ["a", "b", "A"]})[1]
    words = [f"word {n}" for n in range(30)]
    options, error = bingo.validate_setup({"mode": "terms", "terms": words + ["  word 1 "]})
    assert error is None and len(options["items"]) == 30
    with pytest.raises(ValueError):
        bingo.new_state(3, None)


def test_number_cards_follow_the_columns_and_have_a_free_centre(clock):
    s = numbers(4)
    for seat in (1, 2, 3):
        card = s["cards"][seat]
        assert card[12] is None and len({c for c in card if c is not None}) == 24
        for sq, item in enumerate(card):
            if item is not None:
                assert item // 15 == sq % 5  # column B holds 1-15, I 16-30, and so on
    assert s["items"][0] == "B 1" and s["items"][74] == "O 75"


def test_term_cards_use_24_of_the_words(clock):
    options, _ = bingo.validate_setup({"mode": "terms", "terms": [f"w{n}" for n in range(40)]})
    s = bingo.new_state(2, options)
    card = s["cards"][1]
    assert card[12] is None and len({c for c in card if c is not None}) == 24


def test_the_server_draws_on_a_timer_without_repeats(clock):
    s = numbers(speed=5)
    assert bingo.tick(s, clock[0])[1] is False  # the first draw waits for the countdown
    clock[0] += bingo.READY_MS
    s, changed = bingo.tick(s, clock[0])
    assert changed and len(s["called"]) == 1
    assert bingo.tick(s, clock[0] + 4000)[1] is False
    for _ in range(74):
        clock[0] += 5000
        bingo.tick(s, clock[0])
    assert sorted(s["called"]) == list(range(75))
    clock[0] += 5000
    s, changed = bingo.tick(s, clock[0])
    assert changed and bingo.result(s) == {"winner": None, "reason": "all_drawn"}


def test_pause_and_draw_now_are_the_hosts(clock):
    s = numbers()
    assert bingo.apply(s, 1, {"pause": True})[1] == "Mark a square, or call bingo."
    bingo.apply(s, 0, {"pause": True})
    clock[0] += 60_000
    assert bingo.tick(s, clock[0])[1] is False
    bingo.apply(s, 0, {"draw": True})
    assert len(s["called"]) == 1
    bingo.apply(s, 0, {"pause": False})
    assert bingo.tick(s, clock[0])[1] is False and s["next_draw_ms"] == clock[0] + 8000


def test_you_can_only_mark_what_was_called(clock):
    s = numbers()
    assert bingo.apply(s, 1, {"mark": 0})[1] == "That has not been called yet."
    assert bingo.apply(s, 1, {"mark": 12})[1] == "Pick a square on your card."
    assert bingo.apply(s, 1, {"mark": 25})[1] == "Pick a square on your card."
    call_on_card(s, 1, [0])
    assert bingo.apply(s, 1, {"mark": 0})[1] is None and 0 in s["marks"][1]
    bingo.apply(s, 1, {"mark": 0})
    assert 0 not in s["marks"][1]  # tapping again takes it off


def test_a_wrong_claim_costs_ten_seconds_and_a_right_one_wins(clock):
    s = numbers()
    assert "Not yet" in bingo.apply(s, 1, {"bingo": True})[1]
    assert bingo.apply(s, 1, {"bingo": True})[1] == "Wait a few seconds before you try again."
    clock[0] += bingo.CLAIM_PENALTY_MS
    call_on_card(s, 1, [0, 1, 2, 3, 4])
    assert "Not yet" in bingo.apply(s, 1, {"bingo": True})[1]  # called but not marked
    clock[0] += bingo.CLAIM_PENALTY_MS
    for sq in range(5):
        bingo.apply(s, 1, {"mark": sq})
    assert bingo.apply(s, 1, {"bingo": True})[1] is None
    out = bingo.result(s)
    assert out["winner"] == 1 and out["reason"] == "bingo" and out["line"] == [0, 1, 2, 3, 4]
    assert bingo.apply(s, 2, {"bingo": True})[1] == "The game is over."


def test_the_free_centre_counts_and_diagonals_win(clock):
    s = numbers()
    call_on_card(s, 1, [0, 6, 18, 24])
    for sq in (0, 6, 18, 24):
        bingo.apply(s, 1, {"mark": sq})
    assert bingo.apply(s, 1, {"bingo": True})[1] is None and bingo.result(s)["line"] == [0, 6, 12, 18, 24]


def test_full_card_needs_every_square(clock):
    s = numbers(pattern="full")
    squares = [i for i in range(25) if i != 12]
    call_on_card(s, 1, squares[:-1])
    for sq in squares[:-1]:
        bingo.apply(s, 1, {"mark": sq})
    assert "Not yet" in bingo.apply(s, 1, {"bingo": True})[1]
    clock[0] += bingo.CLAIM_PENALTY_MS
    call_on_card(s, 1, squares[-1:])
    bingo.apply(s, 1, {"mark": squares[-1]})
    assert bingo.apply(s, 1, {"bingo": True})[1] is None


def test_each_player_only_sees_their_own_card(clock):
    s = numbers(3)
    one, two, host = bingo.view(s, 1), bingo.view(s, 2), bingo.view(s, 0)
    assert len(one["card"]) == 25 and one["card"] != two["card"] and "card" not in host
    assert host["host"] and not one["host"]
    bingo.add_player(s, 3)
    assert len(bingo.view(s, 3)["card"]) == 25


def test_a_hosted_bingo_through_the_server(env, clock):  # noqa: F811
    client, ids, _ = env
    with app.dependency_overrides[get_sessionmaker]()() as db:
        db.add(Game(name="bingo", owner_id=ids["ada"], repo_url="https://github.com/ada/bingo"))
        db.commit()
    with client.websocket_connect(url(ids["ada"], game="bingo")) as host:
        code = host.receive_json()["room"]
        host.send_json({"type": "setup", "data": {"mode": "numbers", "speed": 1}})
        assert host.receive_json()["code"] == "bad_setup"
        host.send_json({"type": "setup", "data": {"mode": "numbers"}})
        assert host.receive_json()["type"] == "setup_ok"
        with client.websocket_connect(url(ids["tunde"], room=code, game="bingo")) as player:
            player.receive_json()
            host.receive_json()
            host.send_json({"type": "start"})
            welcome = player.receive_json()
            assert welcome["state"]["card"][12]["label"] == "Free" and welcome["state"]["called"] == []
            player.send_json({"type": "move", "data": {"mark": 0}})
            assert player.receive_json()["code"] == "bad_move"
