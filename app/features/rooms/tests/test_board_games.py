import pytest

from app.features.rooms.games import connect4 as c4
from app.features.rooms.games import draughts as dr


# ---- connect 4

def drop(state, *columns):
    for column in columns:
        state, error = c4.apply(state, state["turn"], {"column": column})
        assert error is None, error
    return state


def test_connect4_discs_fall_to_the_bottom_and_stack():
    s = drop(c4.new_state(2), 3, 3, 3)
    assert s["board"][5 * 7 + 3] == 0 and s["board"][4 * 7 + 3] == 1 and s["board"][3 * 7 + 3] == 0
    assert s["last"] == {"row": 3, "col": 3} and s["turn"] == 1


def test_connect4_wins_in_every_direction():
    across = drop(c4.new_state(2), 0, 0, 1, 1, 2, 2, 3)
    assert c4.result(across)["winner"] == 0 and c4.result(across)["line"] == [35, 36, 37, 38]
    down = drop(c4.new_state(2), 0, 1, 0, 1, 0, 1, 0)
    assert c4.result(down) == {"winner": 0, "line": [14, 21, 28, 35], "reason": "four"}
    diagonal = drop(c4.new_state(2), 0, 1, 1, 2, 3, 2, 2, 3, 3, 6, 3)  # seat 0 builds a rising diagonal
    assert c4.result(diagonal)["winner"] == 0 and len(c4.result(diagonal)["line"]) == 4
    assert c4.turn(diagonal) is None


def test_connect4_refuses_bad_moves():
    s = c4.new_state(2)
    assert c4.apply(s, 1, {"column": 0})[1] == "It's not your turn."
    assert c4.apply(s, 0, {"column": 7})[1] == "Pick a column from 0 to 6."
    assert c4.apply(s, 0, {"column": "3"})[1] == "Pick a column from 0 to 6."
    full = drop(s, 0, 0, 0, 0, 0, 0)
    assert c4.apply(full, 0, {"column": 0})[1] == "That column is full."


def test_connect4_draw_when_the_board_is_full():
    s = c4.new_state(2)
    pattern = "0011001" "1100110" "0011001" "1100110" "0011001" "1100110"  # no four in a row anywhere
    s["board"] = [int(ch) for ch in pattern]
    s["last"] = {"row": 5, "col": 6}
    assert c4.result(s) == {"winner": None, "reason": "draw"}


def test_connect4_bot_wins_blocks_and_stays_legal():
    win = drop(c4.new_state(2), 0, 6, 1, 6, 2, 5)  # seat 0 can play column 3 to win
    assert c4.bot_move(win, 0)["column"] == 3
    block = drop(c4.new_state(2), 0, 1, 0, 2, 0, 6)  # seat 0 threatens column 0; seat 1's bot must block it
    assert c4.bot_move(block, 1)["column"] == 0
    s = c4.new_state(2)
    for _ in range(12):
        s, error = c4.apply(s, s["turn"], c4.bot_move(s, s["turn"], "easy"))
        assert error is None
        if c4.result(s):
            break


# ---- draughts

def put(pieces, turn=0):
    """A position from {"c3": "m0", ...}."""
    s = dr.new_state(2)
    s["board"] = [None] * 64
    for name, piece in pieces.items():
        s["board"][dr.sq(name)] = piece
    s["turn"] = turn
    return s


def go(state, *squares):
    new, error = dr.apply(state, state["turn"], {"path": list(squares)})
    assert error is None, error
    return new


def test_draughts_starts_with_twelve_men_each_and_seven_opening_moves():
    s = dr.new_state(2)
    assert sum(1 for p in s["board"] if p == "m0") == 12 and sum(1 for p in s["board"] if p == "m1") == 12
    assert all(dr._dark(i // 8, i % 8) for i, p in enumerate(s["board"]) if p)
    assert len(dr.legal_moves(s)) == 7


def test_draughts_men_step_forward_only_and_turns_alternate():
    s = go(dr.new_state(2), "c3", "d4")
    assert s["board"][dr.sq("d4")] == "m0" and s["turn"] == 1
    assert dr.apply(s, 0, {"path": ["d4", "e5"]})[1] == "It's not your turn."
    assert dr.apply(dr.new_state(2), 0, {"path": ["c3", "b2"]})[1] == "That move isn't allowed."  # backwards and occupied
    assert dr.apply(dr.new_state(2), 0, {"path": ["c3", "c4"]})[1] == "That move isn't allowed."  # not diagonal


def test_draughts_a_capture_is_compulsory():
    s = put({"c3": "m0", "d4": "m1", "a1": "m0"})  # c3 can jump d4; a1 could just step
    assert dr.apply(s, 0, {"path": ["a1", "b2"]})[1] == "You have to capture."
    after = go(s, "c3", "e5")
    assert after["board"][dr.sq("d4")] is None and after["board"][dr.sq("e5")] == "m0"
    assert dr.view(s, 0)["must_capture"] is True


def test_draughts_multiple_jumps_must_be_finished():
    s = put({"a1": "m0", "b2": "m1", "d4": "m1", "h8": "m1"})
    assert dr.apply(s, 0, {"path": ["a1", "c3"]})[1] == "You have to capture."  # stopping after one jump is not a move: finish the jump
    after = go(s, "a1", "c3", "e5")
    assert after["board"][dr.sq("b2")] is None and after["board"][dr.sq("d4")] is None and after["board"][dr.sq("e5")] == "m0"


def test_draughts_a_man_that_reaches_the_far_rank_becomes_a_king_and_the_turn_ends():
    s = put({"a7": "m0", "h1": "m1"})
    k = go(s, "a7", "b8")
    assert k["board"][dr.sq("b8")] == "k0"
    # crowning in the middle of a jump ends the jump even if another capture would follow
    s2 = put({"c5": "m0", "d6": "m1", "d8": "m1", "f8": "m1", "h2": "m1"})
    ended = go(s2, "c5", "e7")  # a man cannot reach the back rank here; this is a plain jump
    assert ended["board"][dr.sq("e7")] == "m0"
    s3 = put({"d6": "m0", "e7": "m1", "g7": "m1", "a1": "m1"})
    crown = go(s3, "d6", "f8")
    assert crown["board"][dr.sq("f8")] == "k0" and crown["board"][dr.sq("g7")] == "m1"  # g7 is still there: the turn ended


def test_draughts_kings_move_and_capture_both_ways():
    s = put({"e5": "k0", "d4": "m1", "f6": "m1", "h8": "m1"})
    moves = dr.legal_moves(s)
    assert all(m["captures"] for m in moves)  # a capture is possible, so only captures are offered
    after = go(s, "e5", "c3")
    assert after["board"][dr.sq("d4")] is None and after["board"][dr.sq("c3")] == "k0"
    man = put({"e5": "m0", "d4": "m1"})  # a man cannot jump backwards
    assert dr.legal_moves(man) and not any(m["captures"] for m in dr.legal_moves(man))


def test_draughts_no_moves_loses_and_the_quiet_rule_draws():
    s = put({"a1": "m0"}, turn=1)  # seat 1 has no pieces
    assert dr.result(s) == {"winner": 0, "reason": "no_moves"} and dr.turn(s) is None
    blocked = put({"a1": "m0", "b2": "m1", "c3": "m1"}, turn=0)  # the man cannot step, and cannot jump b2 because c3 is taken
    assert dr.result(blocked) == {"winner": 1, "reason": "no_moves"}
    quiet = put({"a1": "k0", "h8": "k1"})
    quiet["halfmove"] = dr.MAX_QUIET
    assert dr.result(quiet) == {"winner": None, "reason": "forty_moves"}


def test_draughts_bad_input_is_refused():
    s = dr.new_state(2)
    assert dr.apply(s, 0, {"path": ["c3"]})[1].startswith("Send the squares")
    assert dr.apply(s, 0, {"path": ["z9", "a1"]})[1] == "Use squares like c3 and d4."
    assert dr.apply(s, 0, "c3d4")[1].startswith("Send the squares")


@pytest.mark.parametrize("level", ["easy", "medium"])
def test_draughts_bot_plays_legal_games(level):
    s = dr.new_state(2)
    for _ in range(40):
        if dr.result(s):
            break
        move = dr.bot_move(s, s["turn"], level)
        s, error = dr.apply(s, s["turn"], move)
        assert error is None, error


def test_draughts_medium_bot_takes_a_free_piece():
    s = put({"c3": "m0", "d4": "m1", "a1": "m0", "h8": "m1"})
    assert dr.bot_move(s, 0)["path"] == ["c3", "e5"]
