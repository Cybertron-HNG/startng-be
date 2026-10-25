import random

import pytest

from app.features.rooms import core
from app.features.rooms.games import ayo, ludo, whot
from app.main import app
from app.models import Game
from app.routers.live import get_sessionmaker

from .test_rooms import env, url  # noqa: F401


def pits(**filled):
    """An ayo state with only the listed pits holding seeds (p0=4 means pit 0 holds 4)."""
    state = ayo.new_state(2)
    state["pits"] = [0] * 12
    for key, count in filled.items():
        state["pits"][int(key[1:])] = count
    return state


# ---- ayo

def test_ayo_sows_counter_clockwise_and_skips_the_pit_it_started_from():
    s, error = ayo.apply(ayo.new_state(2), 0, {"pit": 0})
    assert error is None and s["pits"][:6] == [0, 5, 5, 5, 5, 4] and s["turn"] == 1
    big = pits(p0=12, p6=1)
    new, _ = ayo.apply(big, 0, {"pit": 0})
    assert new["pits"][0] == 0 and new["pits"][1] == 2 and sum(new["pits"]) == 13


def test_ayo_captures_twos_and_threes_and_the_run_behind_them():
    s = pits(p0=3, p5=2, p6=1, p7=2, p8=1)
    new, error = ayo.apply(s, 0, {"pit": 5})
    assert error is None and new["scores"] == [5, 0] and new["pits"][6:8] == [0, 0] and new["pits"][8] == 1
    assert new["last"]["captured"] == [7, 6]


def test_ayo_does_not_capture_when_it_would_take_every_seed():
    s = pits(p0=3, p5=2, p6=1, p7=2)
    new, _ = ayo.apply(s, 0, {"pit": 5})
    assert new["scores"] == [0, 0] and new["pits"][6:8] == [2, 3]


def test_ayo_has_to_feed_an_empty_opponent():
    s = pits(p0=1, p5=1)
    assert ayo.legal(s, 0) == [5]
    assert ayo.apply(s, 0, {"pit": 0})[1].startswith("Your opponent has no seeds")
    assert ayo.apply(s, 0, {"pit": 2})[1] == "That pit is empty."


def test_ayo_ends_when_a_player_cannot_play_and_the_other_keeps_their_seeds():
    s = pits(p0=1, p1=2, p7=1)
    s["scores"] = [10, 10]
    s["turn"] = 0
    new, error = ayo.apply(s, 0, {"pit": 1})  # seat 1 is left with a seed, so the game goes on
    assert error is None and new["end"] is None
    stuck = pits(p0=1)
    stuck["turn"] = 1  # seat 1 has nothing and seat 0 cannot feed them from pit 0
    ayo._end(stuck)
    assert stuck["end"]["reason"] == "no_moves" and stuck["end"]["winner"] == 0 and stuck["scores"] == [1, 0]


def test_ayo_first_to_25_wins_and_a_draw_is_possible():
    s = pits(p0=3, p5=2, p6=1, p7=2, p8=1)
    s["scores"] = [20, 0]
    new, _ = ayo.apply(s, 0, {"pit": 5})
    assert new["end"] == {"winner": 0, "reason": "captured", "scores": [25, 0]}
    even = pits(p0=1, p6=1)
    even["scores"] = [23, 23]
    even["halfmove"] = ayo.MAX_QUIET
    ayo._end(even)
    assert even["end"]["winner"] is None and even["scores"] == [24, 24]


def test_ayo_refuses_bad_moves():
    s = ayo.new_state(2)
    assert ayo.apply(s, 1, {"pit": 0})[1] == "It's not your turn."
    assert ayo.apply(s, 0, {"pit": 6})[1] == "Pick one of your pits, 0 to 5."
    assert ayo.apply(s, 0, {"pit": "1"})[1] == "Pick one of your pits, 0 to 5."


@pytest.mark.parametrize("level", ["easy", "medium"])
def test_ayo_bots_finish_a_game(level):
    s = ayo.new_state(2)
    for _ in range(600):
        if ayo.result(s):
            break
        seat = ayo.turn(s)
        s, error = ayo.apply(s, seat, ayo.bot_move(s, seat, level))
        assert error is None, error
    assert ayo.result(s) is not None and sum(s["pits"]) + sum(s["scores"]) == 48


# ---- whot

def test_whot_deck_deal_and_view():
    s = whot.new_state(3)
    assert len(s["market"]) + len(s["discard"]) + sum(len(h) for h in s["hands"].values()) == 54
    assert all(len(h) == 5 for h in s["hands"].values()) and len(whot.new_state(2)["hands"][0]) == 6
    assert s["discard"][0][1] not in whot.SPECIAL
    mine, theirs = whot.view(s, 0), whot.view(s, 1)
    assert mine["hand"] == s["hands"][0] and mine["hand"] != theirs["hand"] and "hands" not in mine and mine["counts"] == [5, 5, 5]


def hand(state, seat, *cards):
    state["hands"][seat] = [list(c) for c in cards]


def test_whot_matches_shape_or_number_and_whot_names_a_shape():
    s = whot.new_state(2)
    s["discard"] = [["circle", 4]]
    s["shape"] = "circle"
    hand(s, 0, ("circle", 10), ("cross", 4), ("square", 7), ("whot", 20), ("star", 3))
    assert whot.playable(s, 0) == [0, 1, 3]
    assert whot.apply(s, 0, {"play": 2})[1] == "That card does not match the shape or the number."
    assert whot.apply(s, 0, {"play": 3})[1] == "Say which shape you want."
    s, error = whot.apply(s, 0, {"play": 3, "shape": "star"})
    assert error is None and s["shape"] == "star" and s["turn"] == 1
    hand(s, 1, ("star", 1), ("circle", 20 - 20 + 4))
    assert whot.playable(s, 1) == [0]  # after a Whot only the named shape counts


def test_whot_pick_two_stacks_and_a_player_picks_what_built_up():
    s = whot.new_state(3)
    s["discard"], s["shape"] = [["circle", 4]], "circle"
    hand(s, 0, ("circle", 2), ("circle", 9 + 1))
    hand(s, 1, ("cross", 2), ("star", 3))
    hand(s, 2, ("star", 4))
    s, _ = whot.apply(s, 0, {"play": 0})
    assert s["pending"] == {"kind": 2, "count": 1} and whot.view(s, 1)["pending"] == {"kind": 2, "cards": 2}
    assert whot.playable(s, 1) == [0]
    assert "pick up" in whot.apply(s, 1, {"play": 1})[1]
    s, _ = whot.apply(s, 1, {"play": 0})
    assert whot.view(s, 2)["pending"]["cards"] == 4
    before = len(s["hands"][2])
    s, _ = whot.apply(s, 2, {"draw": True})
    assert len(s["hands"][2]) == before + 4 and s["pending"] is None and s["turn"] == 0


def test_whot_hold_on_general_market_and_suspension():
    s = whot.new_state(3)
    s["discard"], s["shape"] = [["circle", 4]], "circle"
    hand(s, 0, ("circle", 1), ("circle", 14), ("circle", 8), ("circle", 3))
    sizes = [len(s["hands"][1]), len(s["hands"][2])]
    s, _ = whot.apply(s, 0, {"play": 0})
    assert s["turn"] == 0  # hold on
    s, _ = whot.apply(s, 0, {"play": 0})  # the 14 (it moved to place 0)
    assert s["turn"] == 0 and [len(s["hands"][1]), len(s["hands"][2])] == [n + 1 for n in sizes]
    s, _ = whot.apply(s, 0, {"play": 0})  # the 8: seat 1 misses a turn
    assert s["turn"] == 2


def test_whot_the_first_to_empty_their_hand_wins_and_everyone_sees_the_hands():
    s = whot.new_state(2)
    s["discard"], s["shape"] = [["circle", 4]], "circle"
    hand(s, 0, ("circle", 7))
    s, _ = whot.apply(s, 0, {"play": 0})
    assert whot.result(s) == {"winner": 0, "reason": "emptied"} and "hands" in whot.view(s, 1)
    assert whot.apply(s, 1, {"draw": True})[1] == "The game is over."


def test_whot_reshuffles_the_pile_when_the_market_runs_out():
    s = whot.new_state(2)
    s["discard"] = [["circle", 1 + n] for n in range(5)]
    s["market"] = []
    s, _ = whot.apply(s, 0, {"draw": True})
    assert len(s["hands"][0]) == 7 and len(s["discard"]) == 1 and len(s["market"]) == 3


@pytest.mark.parametrize("players", [2, 3, 4])
def test_whot_bots_finish_a_game(players):
    s = whot.new_state(players)
    for _ in range(3000):
        if whot.result(s):
            break
        seat = whot.turn(s)
        s, error = whot.apply(s, seat, whot.bot_move(s, seat, "medium"))
        assert error is None, error
    assert whot.result(s) is not None


# ---- ludo

class Die:
    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randint(self, a, b):
        return self.rolls.pop(0)


def test_ludo_needs_a_six_to_leave_the_base_and_a_six_rolls_again():
    s = ludo.new_state(2)
    s["rng"] = Die(3, 6)
    s, _ = ludo.apply(s, 0, {"roll": True})
    assert s["turn"] == 1 and s["die"] is None and s["last"]["moved"] is None  # no move with a 3: the turn passes
    s["turn"] = 0
    s, _ = ludo.apply(s, 0, {"roll": True})
    assert s["die"] == 6 and ludo.view(s, 0)["movable"] == [0, 1, 2, 3]
    assert ludo.apply(s, 1, {"move": 0})[1] == "It's not your turn."
    s, error = ludo.apply(s, 0, {"move": 2})
    assert error is None and s["tokens"][0] == [-1, -1, 0, -1] and s["turn"] == 0 and s["die"] is None


def test_ludo_three_sixes_lose_the_turn():
    s = ludo.new_state(2)
    s["rng"] = Die(6, 6, 6)
    for token in (0, 1):
        s, _ = ludo.apply(s, 0, {"roll": True})
        s, _ = ludo.apply(s, 0, {"move": token})
    s, _ = ludo.apply(s, 0, {"roll": True})
    assert s["turn"] == 1 and s["last"]["note"] == "three sixes"


def test_ludo_a_capture_sends_the_token_home_but_safe_squares_protect():
    s = ludo.new_state(2)  # offsets 0 and 26
    s["tokens"][0] = [20, -1, -1, -1]
    s["tokens"][1] = [-1, -1, -1, 18]  # seat 1's position 18 is square 44... move so they meet on square 23
    s["tokens"][1][3] = -1
    s["tokens"][1][0] = 23 - 26 + 52  # absolute square 23
    s["rng"] = Die(3)
    s, _ = ludo.apply(s, 0, {"roll": True})
    s, _ = ludo.apply(s, 0, {"move": 0})
    assert s["tokens"][1][0] == -1 and s["last"]["captured"] == [[1, 0]] and s["turn"] == 0  # a capture gives another roll
    safe = ludo.new_state(2)
    safe["tokens"][0] = [5, -1, -1, -1]
    safe["tokens"][1][0] = 8 - 26 + 52  # absolute 8 is a star square
    safe["rng"] = Die(3)
    safe, _ = ludo.apply(safe, 0, {"roll": True})
    safe, _ = ludo.apply(safe, 0, {"move": 0})
    assert safe["tokens"][1][0] == 8 - 26 + 52 and safe["last"]["captured"] == []


def test_ludo_needs_the_exact_number_to_finish_and_the_first_home_wins():
    s = ludo.new_state(2)
    s["tokens"][0] = [ludo.HOME, ludo.HOME, ludo.HOME, 53]
    s["rng"] = Die(4, 3)
    s, _ = ludo.apply(s, 0, {"roll": True})
    assert s["turn"] == 1  # 53 + 4 would pass the end: no move
    s["turn"] = 0
    s, _ = ludo.apply(s, 0, {"roll": True})
    s, _ = ludo.apply(s, 0, {"move": 3})
    assert ludo.result(s) == {"winner": 0, "reason": "home"}


@pytest.mark.parametrize("players", [2, 3, 4])
def test_ludo_bots_finish_a_game(players):
    s = ludo.new_state(players)
    for _ in range(20000):
        if ludo.result(s):
            break
        seat = ludo.turn(s)
        s, error = ludo.apply(s, seat, ludo.bot_move(s, seat, "medium"))
        assert error is None, error
    assert ludo.result(s) is not None


# ---- the rooms server with a game that the creator starts

@pytest.fixture
def live(env):  # noqa: F811
    client, ids, now = env
    with app.dependency_overrides[get_sessionmaker]()() as db:
        for name in ("ludo", "whot"):
            db.add(Game(name=name, owner_id=ids["ada"], repo_url=f"https://github.com/ada/{name}"))
        db.commit()
    return client, ids, now


def test_the_creator_fills_seats_with_bots_and_starts_a_ludo_game(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], game="ludo")) as host:
        welcome = host.receive_json()
        code = welcome["room"]
        assert welcome["host"] == 0 and welcome["players"][0]["host"] is True
        host.send_json({"type": "start"})
        assert host.receive_json()["code"] == "need_players"
        host.send_json({"type": "bot"})
        assert host.receive_json()["type"] == "join"
        with client.websocket_connect(url(ids["tunde"], room=code, game="ludo")) as tunde:
            assert tunde.receive_json()["you"] == 2
            host.receive_json()
            tunde.send_json({"type": "start"})  # only the creator can start it
            tunde.send_json({"type": "bot"})
            tunde.send_json({"type": "ping"})
            assert tunde.receive_json()["type"] == "pong"
            host.send_json({"type": "bot"})  # a fourth seat
            host.receive_json(), tunde.receive_json()
            host.send_json({"type": "start"})
            started = host.receive_json()
            assert started["status"] == "playing" and started["state"]["can_roll"] is True and started["turn"] == 0
            with client.websocket_connect(url(ids["chi"], room=code, game="ludo")) as late:
                pass
        assert len(core.rooms[code].seats) == 4


def test_a_player_who_stays_away_too_long_is_replaced_by_a_bot(live):
    client, ids, now = live
    with client.websocket_connect(url(ids["ada"], game="whot")) as host:
        code = host.receive_json()["room"]
        with client.websocket_connect(url(ids["tunde"], room=code, game="whot")) as tunde:
            tunde.receive_json()
            host.receive_json()
            host.send_json({"type": "start"})
            tunde.receive_json()
            host.receive_json()
        host.receive_json()  # away
        now[0] += core.SEAT_GRACE + 1
        client.portal.call(core.sweep)
        assert host.receive_json()["type"] == "join"
        assert core.rooms[code].seats[1].bot is True and core.rooms[code].status == "playing"


def test_a_two_player_game_without_a_start_still_starts_when_full(env):  # noqa: F811
    client, ids, _ = env
    with client.websocket_connect(url(ids["ada"])) as ada:
        code = ada.receive_json()["room"]
        assert ada.receive_json is not None
        with client.websocket_connect(url(ids["tunde"], code)) as tunde:
            assert tunde.receive_json()["status"] == "playing"
