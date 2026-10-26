import pytest

from app.features.rooms.games import chess as c


def play(state, *moves):
    """Plays moves like "e2e4" (or "e7e8q") for whoever's turn it is; each must be legal."""
    for m in moves:
        move = {"from": m[:2], "to": m[2:4]}
        if len(m) == 5:
            move["promotion"] = m[4]
        state, error = c.apply(state, state["turn"], move)
        assert error is None, f"{m}: {error}"
    return state


def test_perft_counts_match_the_standard_ones():
    def perft(state, depth):
        moves = c.legal_moves(state)
        return len(moves) if depth == 1 else sum(perft(c._make(state, m), depth - 1) for m in moves)

    assert [perft(c.new_state(2), d) for d in (1, 2, 3)] == [20, 400, 8902]
    kiwipete = c.from_fen("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
    assert [perft(kiwipete, d) for d in (1, 2)] == [48, 2039]  # castling, pins, en passant all at once
    pos3 = c.from_fen("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1")
    assert [perft(pos3, d) for d in (1, 2, 3)] == [14, 191, 2812]
    promo = c.from_fen("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1")
    assert [perft(promo, d) for d in (1, 2)] == [6, 264]


def test_turns_and_illegal_moves_are_refused():
    s = c.new_state(2)
    assert c.apply(s, 1, {"from": "e7", "to": "e5"})[1] == "It's not your turn."
    assert c.apply(s, 0, {"from": "e2", "to": "e5"})[1] == "That move isn't allowed."  # a pawn can't jump three
    assert c.apply(s, 0, {"from": "a1", "to": "a3"})[1] == "That move isn't allowed."  # the rook is blocked
    assert c.apply(s, 0, {"from": "z9", "to": "e4"})[1] == "Use squares like e2 and e4."
    assert c.apply(s, 0, "e2e4")[1] == "That isn't a move."
    s, error = c.apply(s, 0, {"from": "e2", "to": "e4"})
    assert error is None and s["turn"] == 1 and s["ep"] == c.sq("e3")


def test_scholars_mate():
    s = play(c.new_state(2), "e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7")
    assert c.result(s) == {"winner": 0, "reason": "checkmate"} and c.turn(s) is None
    assert c.apply(s, 1, {"from": "a7", "to": "a6"})[1] == "The game is over."


def test_a_pinned_piece_cannot_move_and_check_must_be_answered():
    s = c.from_fen("4k3/8/8/8/8/8/4r3/4K2R w - - 0 1")  # the white king is in check from the rook on e2
    assert c.in_check(s)
    assert c.apply(s, 0, {"from": "h1", "to": "h2"})[1] == "That move isn't allowed."
    assert c.apply(s, 0, {"from": "e1", "to": "e2"})[1] is None  # capturing the checking rook
    pinned = c.from_fen("4k3/4r3/8/8/8/8/4B3/4K3 w - - 0 1")  # the bishop is pinned to its king
    assert c.apply(pinned, 0, {"from": "e2", "to": "d3"})[1] == "That move isn't allowed."


def test_stalemate_is_a_draw():
    s = c.from_fen("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert c.result(s) == {"winner": None, "reason": "stalemate"}


def test_castling_both_ways_and_its_rules():
    s = c.from_fen("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    kingside = play(s, "e1g1")
    assert kingside["board"][c.sq("g1")] == "wK" and kingside["board"][c.sq("f1")] == "wR" and kingside["castling"] == "kq"
    queenside = play(s, "e1c1")
    assert queenside["board"][c.sq("c1")] == "wK" and queenside["board"][c.sq("d1")] == "wR"
    # not through check, not out of check, not after the rook has moved
    guarded = c.from_fen("r3k2r/8/8/8/8/8/5r2/R3K2R w KQkq - 0 1")
    assert c.apply(guarded, 0, {"from": "e1", "to": "g1"})[1] == "That move isn't allowed."
    moved = play(s, "h1h2", "a8a7", "h2h1", "a7a8")
    assert c.apply(moved, 0, {"from": "e1", "to": "g1"})[1] == "That move isn't allowed."
    assert c.apply(moved, 0, {"from": "e1", "to": "c1"})[1] is None


def test_en_passant_only_straight_after_the_double_step():
    s = play(c.new_state(2), "e2e4", "a7a6", "e4e5", "d7d5")
    taken = play(s, "e5d6")
    assert taken["board"][c.sq("d5")] is None and taken["board"][c.sq("d6")] == "wP"
    late = play(s, "a2a3", "a6a5")
    assert c.apply(late, 0, {"from": "e5", "to": "d6"})[1] == "That move isn't allowed."


def test_promotion_defaults_to_a_queen_and_can_be_chosen():
    s = c.from_fen("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    assert play(s, "a7a8")["board"][c.sq("a8")] == "wQ"
    assert play(s, "a7a8n")["board"][c.sq("a8")] == "wN"
    assert c.apply(s, 0, {"from": "a7", "to": "a8", "promotion": "k"})[1] == "Promote to q, r, b or n."


def test_draws_that_happen_by_themselves():
    assert c.result(c.from_fen("8/8/4k3/8/8/4K3/8/8 w - - 0 1"))["reason"] == "insufficient_material"
    assert c.result(c.from_fen("8/8/4k3/8/8/4K3/6N1/8 w - - 0 1"))["reason"] == "insufficient_material"
    assert c.result(c.from_fen("8/8/4k3/8/8/4K3/6R1/8 w - - 0 1")) is None
    assert c.result(c.from_fen("8/8/4k3/8/8/4K3/6R1/8 w - - 100 80"))["reason"] == "fifty_moves"
    s = c.new_state(2)
    for _ in range(2):
        s = play(s, "g1f3", "g8f6", "f3g1", "f6g8")
    assert c.result(s) == {"winner": None, "reason": "repetition"}


def test_view_lists_where_pieces_can_go():
    v = c.view(c.new_state(2), 0)
    assert sorted(v["legal"]["e2"]) == ["e3", "e4"] and sorted(v["legal"]["g1"]) == ["f3", "h3"] and "e7" not in v["legal"]
    assert v["check"] is False and v["turn"] == 0


@pytest.mark.parametrize("level", ["easy", "medium"])
def test_the_bot_plays_legal_moves(level):
    s = c.new_state(2)
    for _ in range(6):
        move = c.bot_move(s, s["turn"], level)
        s, error = c.apply(s, s["turn"], move)
        assert error is None


def test_the_medium_bot_takes_a_free_queen_and_mates_in_one():
    s = c.from_fen("4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1")
    assert c.bot_move(s, 0)["to"] == "d5"
    mate = c.from_fen("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    move = c.bot_move(mate, 0)
    s, error = c.apply(mate, 0, move)
    assert error is None and c.result(s) == {"winner": 0, "reason": "checkmate"}
