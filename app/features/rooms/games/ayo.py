"""Ayo (also called Ayo Olopon or Oware), the Yoruba and West African seed game for two, refereed by the server.

Rules used (also written on the game's page):
- Two rows of six pits, four seeds in each (48 seeds). Seat 0 owns the bottom row, seat 1 the top row, and seeds travel counter-clockwise:
  along your own row from left to right, then back along your opponent's row from right to left.
- On your turn you pick one of your pits that has seeds, lift all of them and drop them one by one in the pits after it. With 12 or more
  seeds the pit you lifted from is skipped.
- If your last seed lands in one of your opponent's pits and makes it hold exactly 2 or 3, you capture those seeds, and also the pit before it
  if it is your opponent's and now holds 2 or 3, and so on backwards as long as the run does not break. Captured seeds are your score.
- If a capture would take all of your opponent's seeds, you capture nothing that turn (the move is still played).
- If your opponent has no seeds you must, when you can, play a move that gives them some. If you cannot, the game ends and you keep the
  seeds still on your side.
- The first to capture 25 or more seeds wins. If a player has no seeds on their turn, the game ends and their opponent keeps the seeds still
  on their own side. If 100 turns pass with no capture, each player keeps the seeds on their own side. 24 each is a draw.

A move is {"pit": 0-5}: your pits numbered 0 to 5 in the order you sow along your own row. State: {pits: [12 seeds, seat 0's six first then
seat 1's six, both in sowing order], scores: [s0, s1], turn, halfmove, last: {pit, from, captured: [pit numbers]} | None, end: None | result}.
"""
import random

name = "ayo"
min_players = 2
max_players = 2

PITS = 12
WIN = 25
MAX_QUIET = 100


def new_state(players: int) -> dict:
    return {"pits": [4] * PITS, "scores": [0, 0], "turn": 0, "halfmove": 0, "last": None, "end": None}


def _owner(index: int) -> int:
    return index // 6


def _sow(pits: list, start: int) -> tuple[list, int]:
    """Lifts the pit and drops its seeds; returns the new pits and where the last seed landed."""
    pits = list(pits)
    seeds, pits[start] = pits[start], 0
    at = start
    while seeds:
        at = (at + 1) % PITS
        if at == start:
            continue
        pits[at] += 1
        seeds -= 1
    return pits, at


def _captures(pits: list, seat: int, last: int) -> list[int]:
    """Which pits the move captured (before the all-seeds exception)."""
    taken = []
    at = last
    while _owner(at) != seat and pits[at] in (2, 3):
        taken.append(at)
        at -= 1
        if at < 0:
            break
    return taken


def _play(state: dict, seat: int, pit: int) -> tuple[list, int, list[int]]:
    start = seat * 6 + pit
    pits, last = _sow(state["pits"], start)
    taken = _captures(pits, seat, last)
    if taken and sum(pits[i] for i in range(PITS) if _owner(i) != seat) == sum(pits[i] for i in taken):
        taken = []  # that would take every seed of the opponent: nothing is captured
    gained = sum(pits[i] for i in taken)
    for i in taken:
        pits[i] = 0
    return pits, gained, taken


def legal(state: dict, seat: int) -> list[int]:
    own = [pit for pit in range(6) if state["pits"][seat * 6 + pit] > 0]
    opponent_empty = not any(state["pits"][i] for i in range(PITS) if _owner(i) != seat)
    if not opponent_empty:
        return own
    feeding = []
    for pit in own:
        pits, _ = _sow(state["pits"], seat * 6 + pit)
        if any(pits[i] for i in range(PITS) if _owner(i) != seat):
            feeding.append(pit)
    return feeding


def _end(state: dict) -> None:
    """Decides whether the game is over before the player to move plays."""
    seat = state["turn"]
    scores = state["scores"]
    reason = None
    if max(scores) >= WIN:
        reason = "captured"
    elif not legal(state, seat):
        for i in range(PITS):  # they cannot play (no seeds, or cannot feed): everyone keeps what is on their own side
            scores[_owner(i)] += state["pits"][i]
            state["pits"][i] = 0
        reason = "no_moves"
    elif state["halfmove"] >= MAX_QUIET:
        for i in range(PITS):
            scores[_owner(i)] += state["pits"][i]
            state["pits"][i] = 0
        reason = "quiet"
    if reason:
        winner = 0 if scores[0] > scores[1] else 1 if scores[1] > scores[0] else None
        state["end"] = {"winner": winner, "reason": reason, "scores": list(scores)}


def turn(state: dict) -> int | None:
    return None if state["end"] else state["turn"]


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if state["end"]:
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    pit = move.get("pit") if isinstance(move, dict) else None
    if not isinstance(pit, int) or isinstance(pit, bool) or not 0 <= pit < 6:
        return state, "Pick one of your pits, 0 to 5."
    if state["pits"][seat * 6 + pit] == 0:
        return state, "That pit is empty."
    if pit not in legal(state, seat):
        return state, "Your opponent has no seeds: you have to play a move that gives them some."
    pits, gained, taken = _play(state, seat, pit)
    scores = list(state["scores"])
    scores[seat] += gained
    new = {"pits": pits, "scores": scores, "turn": 1 - seat, "halfmove": 0 if gained else state["halfmove"] + 1,
           "last": {"pit": seat * 6 + pit, "captured": taken}, "end": None}
    _end(new)
    return new, None


def result(state: dict) -> dict | None:
    return state["end"]


def view(state: dict, seat: int) -> dict:
    out = dict(state)
    out["moves"] = legal(state, seat) if not state["end"] and state["turn"] == seat else []
    return out


def bot_move(state: dict, seat: int, level: str):
    moves = legal(state, seat)
    if not moves:
        return None
    if level == "easy":
        return {"pit": random.choice(moves)}
    best, best_score = [], None
    for pit in moves:
        pits, gained, _ = _play(state, seat, pit)
        reply = {"pits": pits, "scores": state["scores"], "turn": 1 - seat, "halfmove": 0, "last": None, "end": None}
        risk = max((_play(reply, 1 - seat, p)[1] for p in legal(reply, 1 - seat)), default=0)
        score = gained * 2 - risk
        if best_score is None or score > best_score:
            best, best_score = [pit], score
        elif score == best_score:
            best.append(pit)
    return {"pit": random.choice(best)}
