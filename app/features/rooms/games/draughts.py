"""Draughts (English draughts, also called checkers), refereed by the server.

Rules used (written on the game's page too):
- 8 x 8 board, pieces only on the dark squares (a1 is dark). Each side starts with 12 men: seat 0 on ranks 1 to 3 and moves first,
  seat 1 on ranks 6 to 8.
- A man moves one square diagonally forward. A king moves one square diagonally in any direction.
- A capture jumps a diagonally adjacent enemy piece into the empty square behind it. A man captures forwards only, a king in any
  direction. **A capture is compulsory**: if any capture is possible you must make one, and you must keep jumping with the same piece
  while it can. You may choose which capture.
- A man that reaches the far rank becomes a king, and that ends the turn (even in the middle of a jump).
- You win when the other side has no pieces or no move. 40 moves each (80 turns) with no capture and no man moved is a draw.

A move is {"path": ["c3", "d4"]}: the square the piece is on, then each square it lands on (more than one for a multiple jump). Squares are
numbered a1 = 0, b1 = 1 ... h8 = 63 inside the state. State: {board: [64 x None | "m0" | "k0" | "m1" | "k1"], turn, halfmove, last: [squares]}.
"""
import random

name = "draughts"
min_players = 2
max_players = 2

FILES = "abcdefgh"
STEP = {0: [(1, -1), (1, 1)], 1: [(-1, -1), (-1, 1)]}  # (rank change, file change) for a man
ALL = [(1, -1), (1, 1), (-1, -1), (-1, 1)]
CROWN_RANK = {0: 7, 1: 0}
MAX_QUIET = 80


def label(i: int) -> str:
    return f"{FILES[i % 8]}{i // 8 + 1}"


def sq(text: str) -> int | None:
    if not isinstance(text, str) or len(text) != 2 or text[0] not in FILES or text[1] not in "12345678":
        return None
    return (int(text[1]) - 1) * 8 + FILES.index(text[0])


def _dark(rank: int, file: int) -> bool:
    return (rank + file) % 2 == 0


def new_state(players: int) -> dict:
    board: list = [None] * 64
    for rank in range(8):
        for file in range(8):
            if not _dark(rank, file):
                continue
            if rank <= 2:
                board[rank * 8 + file] = "m0"
            elif rank >= 5:
                board[rank * 8 + file] = "m1"
    return {"board": board, "turn": 0, "halfmove": 0, "last": None}


def _owner(piece: str | None) -> int | None:
    return None if piece is None else int(piece[1])


def _directions(piece: str) -> list[tuple[int, int]]:
    return ALL if piece[0] == "k" else STEP[int(piece[1])]


def _chains(board: list, square: int, piece: str, path: list[int], taken: list[int]) -> list[tuple[list[int], list[int]]]:
    """Every complete capture sequence this piece can make from here: (squares it lands on, squares it captures)."""
    who = int(piece[1])
    rank, file = divmod(square, 8)
    found: list[tuple[list[int], list[int]]] = []
    for dr, df in _directions(piece):
        mid_r, mid_f, land_r, land_f = rank + dr, file + df, rank + 2 * dr, file + 2 * df
        if not (0 <= land_r < 8 and 0 <= land_f < 8):
            continue
        mid, land = mid_r * 8 + mid_f, land_r * 8 + land_f
        enemy = board[mid]
        if enemy is None or _owner(enemy) == who or mid in taken or board[land] is not None:
            continue
        crowned = piece[0] == "m" and land_r == CROWN_RANK[who]
        next_taken = taken + [mid]
        if crowned:  # becoming a king ends the turn
            found.append((path + [land], next_taken))
            continue
        # keep jumping if it can; the piece has left its starting square, which counts as empty now
        trial = list(board)
        trial[square], trial[land] = None, piece
        more = _chains(trial, land, piece, path + [land], next_taken)
        found += more if more else [(path + [land], next_taken)]
    return found


def legal_moves(state: dict, seat: int | None = None) -> list[dict]:
    """The moves the side to move may make: all captures if there is one, else the simple steps. Each is {path, captures}."""
    seat = state["turn"] if seat is None else seat
    board = state["board"]
    captures, steps = [], []
    for i, piece in enumerate(board):
        if piece is None or _owner(piece) != seat:
            continue
        for path, taken in _chains(board, i, piece, [i], []):
            captures.append({"path": path, "captures": taken})
        rank, file = divmod(i, 8)
        for dr, df in _directions(piece):
            r, f = rank + dr, file + df
            if 0 <= r < 8 and 0 <= f < 8 and board[r * 8 + f] is None:
                steps.append({"path": [i, r * 8 + f], "captures": []})
    return captures or steps


def _play(state: dict, move: dict) -> dict:
    board = list(state["board"])
    start, end = move["path"][0], move["path"][-1]
    piece = board[start]
    board[start], board[end] = None, piece
    for taken in move["captures"]:
        board[taken] = None
    if piece[0] == "m" and end // 8 == CROWN_RANK[int(piece[1])]:
        board[end] = "k" + piece[1]
    reset = bool(move["captures"]) or piece[0] == "m"
    return {"board": board, "turn": 1 - state["turn"], "halfmove": 0 if reset else state["halfmove"] + 1, "last": [label(s) for s in move["path"]]}


def turn(state: dict) -> int | None:
    return None if result(state) else state["turn"]


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if result(state):
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    raw = move.get("path") if isinstance(move, dict) else None
    if not isinstance(raw, list) or not 2 <= len(raw) <= 13:
        return state, "Send the squares your piece moves through, like c3 then d4."
    path = [sq(s) for s in raw]
    if None in path:
        return state, "Use squares like c3 and d4."
    moves = legal_moves(state)
    chosen = next((m for m in moves if m["path"] == path), None)
    if chosen is None:
        if any(m["captures"] for m in moves):
            return state, "You have to capture."
        return state, "That move isn't allowed."
    return _play(state, chosen), None


def result(state: dict) -> dict | None:
    if not legal_moves(state):
        return {"winner": 1 - state["turn"], "reason": "no_moves"}
    if state["halfmove"] >= MAX_QUIET:
        return {"winner": None, "reason": "forty_moves"}
    return None


def view(state: dict, seat: int) -> dict:
    """Everything is public. `moves` lists every move the side to move may make as squares, so the page can guide a jump."""
    moves = [[label(s) for s in m["path"]] for m in legal_moves(state)]
    return {
        "board": state["board"], "turn": state["turn"], "last": state["last"], "moves": moves,
        "must_capture": any(m["captures"] for m in legal_moves(state)),
    }


def _material(board: list, seat: int) -> float:
    total = 0.0
    for piece in board:
        if piece:
            value = 1.6 if piece[0] == "k" else 1.0
            total += value if int(piece[1]) == seat else -value
    return total


def bot_move(state: dict, seat: int, level: str = "medium") -> dict | None:
    moves = legal_moves(state)
    if not moves:
        return None
    if level == "easy":
        chosen = random.choice(moves)
    else:
        def score(m: dict) -> float:
            after = _play(state, m)
            replies = legal_moves(after)
            worst = min((_material(_play(after, r)["board"], seat) for r in replies), default=99.0)
            return worst + random.random() * 0.01
        chosen = max(moves, key=score)
    return {"path": [label(s) for s in chosen["path"]]}
