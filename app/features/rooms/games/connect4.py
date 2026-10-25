"""Connect 4. Seat 0 plays first (red), seat 1 second (yellow). A move is {"column": 0-6}: the disc falls to the lowest empty
square. Four in a row, across, down or diagonally, wins; a full board is a draw.

State: {board: [42 x 0 | 1 | None], turn: 0|1, last: {row, col} | None}. Squares are numbered from the top left, row by row (index =
row * 7 + column, row 0 is the top).
"""
import random

name = "connect-4"
min_players = 2
max_players = 2

COLS, ROWS = 7, 6
DIRECTIONS = [(0, 1), (1, 0), (1, 1), (1, -1)]  # right, down, down-right, down-left


def new_state(players: int) -> dict:
    return {"board": [None] * (COLS * ROWS), "turn": 0, "last": None}


def _lowest_free(board: list, column: int) -> int | None:
    for row in range(ROWS - 1, -1, -1):
        if board[row * COLS + column] is None:
            return row
    return None


def _four(board: list, row: int, col: int) -> list[int] | None:
    """The four squares in a row through this disc, if there are four."""
    who = board[row * COLS + col]
    for dr, dc in DIRECTIONS:
        line = [row * COLS + col]
        for sign in (1, -1):
            r, c = row + dr * sign, col + dc * sign
            while 0 <= r < ROWS and 0 <= c < COLS and board[r * COLS + c] == who:
                line.append(r * COLS + c)
                r, c = r + dr * sign, c + dc * sign
        if len(line) >= 4:
            return sorted(line)
    return None


def turn(state: dict) -> int | None:
    return None if result(state) else state["turn"]


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if result(state):
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    column = move.get("column") if isinstance(move, dict) else None
    if not isinstance(column, int) or isinstance(column, bool) or not 0 <= column < COLS:
        return state, "Pick a column from 0 to 6."
    row = _lowest_free(state["board"], column)
    if row is None:
        return state, "That column is full."
    board = list(state["board"])
    board[row * COLS + column] = seat
    return {"board": board, "turn": 1 - seat, "last": {"row": row, "col": column}}, None


def result(state: dict) -> dict | None:
    last = state["last"]
    if last:
        line = _four(state["board"], last["row"], last["col"])
        if line:
            return {"winner": state["board"][last["row"] * COLS + last["col"]], "line": line, "reason": "four"}
    if all(c is not None for c in state["board"]):
        return {"winner": None, "reason": "draw"}
    return None


def view(state: dict, seat: int) -> dict:
    return state  # nothing is hidden


def _wins_for(board: list, column: int, who: int) -> bool:
    row = _lowest_free(board, column)
    if row is None:
        return False
    trial = list(board)
    trial[row * COLS + column] = who
    return _four(trial, row, column) is not None


def bot_move(state: dict, seat: int, level: str = "medium") -> dict | None:
    board = state["board"]
    open_columns = [c for c in range(COLS) if _lowest_free(board, c) is not None]
    if not open_columns:
        return None
    if level != "easy":
        for who in (seat, 1 - seat):  # win if it can, else block
            for c in open_columns:
                if _wins_for(board, c, who):
                    return {"column": c}

        def safe(c: int) -> bool:  # does not hand the opponent a win on the square above
            row = _lowest_free(board, c)
            if row is None or row == 0:
                return True
            trial = list(board)
            trial[row * COLS + c] = seat
            return not _wins_for(trial, c, 1 - seat)

        careful = [c for c in open_columns if safe(c)] or open_columns
        middle_first = sorted(careful, key=lambda c: (abs(c - 3), random.random()))
        return {"column": middle_first[0]}
    return {"column": random.choice(open_columns)}
