"""Tic-tac-toe, the smoke test for the rooms core. Seat 0 is X and moves first; seat 1 is O. A move is {"cell": 0-8}."""
import random

name = "tic-tac-toe"
min_players = 2
max_players = 2

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def new_state(players: int) -> dict:
    return {"board": [None] * 9, "turn": 0}


def _winner(board: list) -> tuple[int, list[int]] | None:
    for line in LINES:
        a, b, c = line
        if board[a] is not None and board[a] == board[b] == board[c]:
            return board[a], list(line)
    return None


def turn(state: dict) -> int | None:
    return None if result(state) else state["turn"]


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if result(state):
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    cell = move.get("cell") if isinstance(move, dict) else None
    if not isinstance(cell, int) or isinstance(cell, bool) or not 0 <= cell <= 8:
        return state, "Pick a square from 0 to 8."
    if state["board"][cell] is not None:
        return state, "That square is taken."
    board = list(state["board"])
    board[cell] = seat
    return {"board": board, "turn": 1 - seat}, None


def result(state: dict) -> dict | None:
    won = _winner(state["board"])
    if won:
        return {"winner": won[0], "line": won[1], "reason": "line"}
    if all(c is not None for c in state["board"]):
        return {"winner": None, "reason": "draw"}
    return None


def view(state: dict, seat: int) -> dict:
    return state  # nothing is hidden


def bot_move(state: dict, seat: int, level: str = "medium") -> dict | None:
    free = [i for i, c in enumerate(state["board"]) if c is None]
    if not free:
        return None
    if level != "easy":
        for who in (seat, 1 - seat):  # win if it can, else block
            for i in free:
                board = list(state["board"])
                board[i] = who
                if _winner(board):
                    return {"cell": i}
        if 4 in free:
            return {"cell": 4}
    return {"cell": random.choice(free)}
