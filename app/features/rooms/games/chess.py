"""Chess, refereed by the server (our own rules code, so there is no library licence to worry about).

Seat 0 is White and moves first; seat 1 is Black. A move is {"from": "e2", "to": "e4"}, plus "promotion": "q" | "r" | "b" | "n" when
a pawn reaches the last rank (a queen when it is left out). Squares are numbered a1 = 0 ... h1 = 7, a2 = 8 ... h8 = 63.

State (JSON-able): {board: [64 x "wP"|"bK"|... | None], turn: 0|1, castling: "KQkq" (what is left), ep: square | None (the square a
pawn may be captured on en passant), halfmove, fullmove, positions: {position key: times seen}, last: {from, to} | None}.
Draws that happen by themselves: stalemate, only kings (or a king and one minor piece) left, the 50 move rule, and the same position
three times. A draw by agreement is not offered yet.
"""
import random

name = "chess"
min_players = 2
max_players = 2

FILES = "abcdefgh"
KNIGHT = [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
KING = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
ROOK = [(1, 0), (0, 1), (-1, 0), (0, -1)]
BISHOP = [(1, 1), (-1, 1), (-1, -1), (1, -1)]
COLOR = "wb"
VALUE = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 0}
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def sq(name_: str) -> int | None:
    if len(name_) != 2 or name_[0] not in FILES or name_[1] not in "12345678":
        return None
    return int(name_[1]) * 8 - 8 + FILES.index(name_[0])


def label(i: int) -> str:
    return f"{FILES[i % 8]}{i // 8 + 1}"


def from_fen(fen: str) -> dict:
    parts = fen.split()
    board: list = [None] * 64
    for r, row in enumerate(parts[0].split("/")):
        f = 0
        for ch in row:
            if ch.isdigit():
                f += int(ch)
            else:
                board[(7 - r) * 8 + f] = ("w" if ch.isupper() else "b") + ch.upper()
                f += 1
    state = {
        "board": board, "turn": 0 if parts[1] == "w" else 1, "castling": "" if parts[2] == "-" else parts[2],
        "ep": None if parts[3] == "-" else sq(parts[3]), "halfmove": int(parts[4]) if len(parts) > 4 else 0,
        "fullmove": int(parts[5]) if len(parts) > 5 else 1, "positions": {}, "last": None,
    }
    state["positions"][_key(state)] = 1
    return state


def new_state(players: int) -> dict:
    return from_fen(START)


def _key(state: dict) -> str:
    """The position for the repetition rule: pieces, who moves, castling rights, and the en passant square."""
    return ",".join(p or "-" for p in state["board"]) + f"|{state['turn']}|{state['castling']}|{state['ep']}"


def _attacked(board: list, target: int, by: int) -> bool:
    """Is the square attacked by a piece of colour `by` (0 white, 1 black)?"""
    c = COLOR[by]
    tf, tr = target % 8, target // 8
    # pawns attack diagonally forward: a white pawn attacks from one rank below
    pr = tr - 1 if by == 0 else tr + 1
    if 0 <= pr < 8:
        for df in (-1, 1):
            pf = tf + df
            if 0 <= pf < 8 and board[pr * 8 + pf] == c + "P":
                return True
    for df, dr in KNIGHT:
        f, r = tf + df, tr + dr
        if 0 <= f < 8 and 0 <= r < 8 and board[r * 8 + f] == c + "N":
            return True
    for df, dr in KING:
        f, r = tf + df, tr + dr
        if 0 <= f < 8 and 0 <= r < 8 and board[r * 8 + f] == c + "K":
            return True
    for dirs, kinds in ((ROOK, "RQ"), (BISHOP, "BQ")):
        for df, dr in dirs:
            f, r = tf + df, tr + dr
            while 0 <= f < 8 and 0 <= r < 8:
                piece = board[r * 8 + f]
                if piece:
                    if piece[0] == c and piece[1] in kinds:
                        return True
                    break
                f, r = f + df, r + dr
    return False


def _king(board: list, color: int) -> int:
    return board.index(COLOR[color] + "K")


def in_check(state: dict, color: int | None = None) -> bool:
    color = state["turn"] if color is None else color
    return _attacked(state["board"], _king(state["board"], color), 1 - color)


def _pseudo(state: dict) -> list[tuple]:
    """Moves that follow the piece rules but may leave the king in check: (from, to, promotion, kind)."""
    board, turn = state["board"], state["turn"]
    me = COLOR[turn]
    moves: list[tuple] = []
    for i, piece in enumerate(board):
        if not piece or piece[0] != me:
            continue
        kind, f, r = piece[1], i % 8, i // 8
        if kind == "P":
            step = 1 if turn == 0 else -1
            start_rank = 1 if turn == 0 else 6
            last_rank = 7 if turn == 0 else 0
            ahead = (r + step) * 8 + f
            if 0 <= r + step < 8 and board[ahead] is None:
                if r + step == last_rank:
                    moves += [(i, ahead, p, "move") for p in "qrbn"]
                else:
                    moves.append((i, ahead, None, "move"))
                    two = (r + 2 * step) * 8 + f
                    if r == start_rank and board[two] is None:
                        moves.append((i, two, None, "double"))
            for df in (-1, 1):
                if not (0 <= f + df < 8 and 0 <= r + step < 8):
                    continue
                t = (r + step) * 8 + f + df
                if board[t] and board[t][0] != me:
                    if r + step == last_rank:
                        moves += [(i, t, p, "capture") for p in "qrbn"]
                    else:
                        moves.append((i, t, None, "capture"))
                elif t == state["ep"]:
                    moves.append((i, t, None, "ep"))
        elif kind in "NK":
            for df, dr in (KNIGHT if kind == "N" else KING):
                nf, nr = f + df, r + dr
                if 0 <= nf < 8 and 0 <= nr < 8:
                    t = nr * 8 + nf
                    if board[t] is None or board[t][0] != me:
                        moves.append((i, t, None, "move"))
            if kind == "K":
                rights = state["castling"]
                home = 4 if turn == 0 else 60
                if i == home and not _attacked(board, home, 1 - turn):
                    k_right, q_right = ("K", "Q") if turn == 0 else ("k", "q")
                    if k_right in rights and board[home + 1] is None and board[home + 2] is None and board[home + 3] == me + "R" \
                            and not _attacked(board, home + 1, 1 - turn) and not _attacked(board, home + 2, 1 - turn):
                        moves.append((i, home + 2, None, "castle"))
                    if q_right in rights and board[home - 1] is None and board[home - 2] is None and board[home - 3] is None \
                            and board[home - 4] == me + "R" and not _attacked(board, home - 1, 1 - turn) \
                            and not _attacked(board, home - 2, 1 - turn):
                        moves.append((i, home - 2, None, "castle"))
        else:
            for df, dr in (ROOK if kind == "R" else BISHOP if kind == "B" else ROOK + BISHOP):
                nf, nr = f + df, r + dr
                while 0 <= nf < 8 and 0 <= nr < 8:
                    t = nr * 8 + nf
                    if board[t] is None:
                        moves.append((i, t, None, "move"))
                    else:
                        if board[t][0] != me:
                            moves.append((i, t, None, "capture"))
                        break
                    nf, nr = nf + df, nr + dr
    return moves


def _make(state: dict, move: tuple) -> dict:
    """The state after a move (the move is assumed legal). Repetition counts are not updated here (see apply)."""
    a, b, promo, kind = move
    board = list(state["board"])
    piece = board[a]
    turn = state["turn"]
    captured = board[b]
    board[b], board[a] = piece, None
    if promo:
        board[b] = piece[0] + promo.upper()
    if kind == "ep":
        board[b - 8 if turn == 0 else b + 8] = None
        captured = "P"
    if kind == "castle":
        if b > a:
            board[a + 1], board[a + 3] = board[a + 3], None
        else:
            board[a - 1], board[a - 4] = board[a - 4], None
    rights = state["castling"]
    for square, right in ((0, "Q"), (7, "K"), (56, "q"), (63, "k")):
        if a == square or b == square:
            rights = rights.replace(right, "")
    if piece[1] == "K":
        rights = "".join(c for c in rights if c.isupper() != (turn == 0))  # the mover loses both of their rights
    return {
        "board": board, "turn": 1 - turn, "castling": rights,
        "ep": (a + b) // 2 if kind == "double" else None,
        "halfmove": 0 if piece[1] == "P" or captured else state["halfmove"] + 1,
        "fullmove": state["fullmove"] + (1 if turn == 1 else 0),
        "positions": state["positions"], "last": {"from": label(a), "to": label(b)},
    }


def legal_moves(state: dict) -> list[tuple]:
    out = []
    mover = state["turn"]
    for move in _pseudo(state):
        after = _make(state, move)
        if not _attacked(after["board"], _king(after["board"], mover), 1 - mover):
            out.append(move)
    return out


def turn(state: dict) -> int | None:
    return None if result(state) else state["turn"]


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if result(state):
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    if not isinstance(move, dict):
        return state, "That isn't a move."
    a, b = sq(str(move.get("from", ""))), sq(str(move.get("to", "")))
    if a is None or b is None:
        return state, "Use squares like e2 and e4."
    promo = str(move.get("promotion") or "").lower() or None
    if promo not in (None, "q", "r", "b", "n"):
        return state, "Promote to q, r, b or n."
    options = [m for m in legal_moves(state) if m[0] == a and m[1] == b]
    if not options:
        return state, "That move isn't allowed."
    chosen = next((m for m in options if m[2] == (promo or "q")), None) if options[0][2] else options[0]
    if chosen is None:
        return state, "That move isn't allowed."
    after = _make(state, chosen)
    positions = dict(state["positions"])
    key = _key(after)
    positions[key] = positions.get(key, 0) + 1
    after["positions"] = positions
    return after, None


def _insufficient(board: list) -> bool:
    pieces = [p for p in board if p and p[1] != "K"]
    if not pieces:
        return True
    return len(pieces) == 1 and pieces[0][1] in "BN"


def result(state: dict) -> dict | None:
    if not legal_moves(state):
        if in_check(state):
            return {"winner": 1 - state["turn"], "reason": "checkmate"}
        return {"winner": None, "reason": "stalemate"}
    if _insufficient(state["board"]):
        return {"winner": None, "reason": "insufficient_material"}
    if state["halfmove"] >= 100:
        return {"winner": None, "reason": "fifty_moves"}
    if state["positions"].get(_key(state), 0) >= 3:
        return {"winner": None, "reason": "repetition"}
    return None


def view(state: dict, seat: int) -> dict:
    """Everything is public. `legal` lists where each piece of the side to move can go, for highlighting."""
    legal: dict[str, list[str]] = {}
    for a, b, p, _ in legal_moves(state):
        legal.setdefault(label(a), [])
        if label(b) not in legal[label(a)]:
            legal[label(a)].append(label(b))
    return {
        "board": state["board"], "turn": state["turn"], "check": in_check(state), "last": state["last"], "legal": legal,
        "fullmove": state["fullmove"],
    }


def _material(board: list, color: int) -> int:
    total = 0
    for p in board:
        if p:
            total += VALUE[p[1]] if p[0] == COLOR[color] else -VALUE[p[1]]
    return total


def _search(state: dict, depth: int, alpha: int, beta: int, me: int) -> int:
    """Negamax with alpha-beta over material, from the side to move's point of view."""
    moves = legal_moves(state)
    if not moves:
        return -100000 if in_check(state) else 0
    if depth == 0:
        return _material(state["board"], state["turn"])
    best = -10 ** 9
    for m in sorted(moves, key=lambda m: m[3] != "capture"):
        score = -_search(_make(state, m), depth - 1, -beta, -alpha, me)
        best = max(best, score)
        alpha = max(alpha, score)
        if alpha >= beta:
            break
    return best


def bot_move(state: dict, seat: int, level: str = "medium") -> dict | None:
    moves = legal_moves(state)
    if not moves:
        return None
    if level == "easy":
        chosen = random.choice([m for m in moves if m[3] == "capture"] or moves)
    else:
        scored = [(-_search(_make(state, m), 1, -10 ** 9, 10 ** 9, seat), m) for m in moves]
        top = max(s for s, _ in scored)
        chosen = random.choice([m for s, m in scored if s == top])
    out = {"from": label(chosen[0]), "to": label(chosen[1])}
    if chosen[2]:
        out["promotion"] = chosen[2]
    return out
