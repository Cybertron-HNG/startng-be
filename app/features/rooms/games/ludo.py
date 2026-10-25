"""Ludo for 2 to 4 players, refereed by the server (the server rolls the die, so nobody can roll their own luck).

Rules used (also written on the game's page):
- Each player has 4 tokens, starting in their base. A 6 brings a token out onto their start square. A token then goes round the 52 squares of the
  ring, and up into its own 5 home squares, and finishes on the last square. A token needs the exact number to finish.
- On your turn you roll the die and move one token that many squares. If you cannot move, the turn passes. A 6 gives you another roll (but a third
  6 in a row loses the turn). Landing on an opponent's token sends it back to its base, and gives you another roll too.
- Start squares and star squares are safe: a token there cannot be sent back. Your own tokens can share a square.
- The first player with all four tokens home wins.

State: {tokens: [[4 positions] per seat], turn, die, ...}. A position is -1 in base, 0 to 50 on the ring counted from that player's own start
square (so absolute square = (offset + position) mod 52, offsets in `offsets`), 51 to 55 on the home squares and 56 finished.
A move is {"roll": true}, then {"move": <token 0 to 3>} when the die has a move to make.
"""
import random

name = "ludo"
start_mode = "start"  # the creator starts it when everyone is in; bots can fill seats
min_players = 2
max_players = 4
forfeit = False

RING = 52
HOME = 56
SAFE = {0, 13, 26, 39, 8, 21, 34, 47}


def _offsets(players: int) -> list[int]:
    return [0, 26] if players == 2 else [0, 13, 26, 39][:players]


def new_state(players: int, options=None) -> dict:
    return {"tokens": [[-1] * 4 for _ in range(players)], "players": players, "offsets": _offsets(players), "turn": 0, "die": None, "sixes": 0,
            "last": None, "end": None, "rng": random.Random()}


def _square(state: dict, seat: int, pos: int) -> int | None:
    return (state["offsets"][seat] + pos) % RING if 0 <= pos <= 50 else None


def movable(state: dict, seat: int, die: int) -> list[int]:
    out = []
    for i, pos in enumerate(state["tokens"][seat]):
        if pos == -1:
            if die == 6:
                out.append(i)
        elif pos + die <= HOME:
            out.append(i)
    return out


def turn(state: dict) -> int | None:
    return None if state["end"] else state["turn"]


def _pass(state: dict, seat: int) -> None:
    state["turn"], state["die"], state["sixes"] = (seat + 1) % state["players"], None, 0


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if state["end"]:
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    if not isinstance(move, dict):
        return state, "Roll the die."
    if state["die"] is None:
        if not move.get("roll"):
            return state, "Roll the die first."
        die = state["rng"].randint(1, 6)
        state["sixes"] = state["sixes"] + 1 if die == 6 else 0
        if state["sixes"] >= 3:
            state["last"] = {"seat": seat, "die": die, "moved": None, "note": "three sixes"}
            _pass(state, seat)
        elif not movable(state, seat, die):
            state["last"] = {"seat": seat, "die": die, "moved": None}
            _pass(state, seat)
        else:
            state["die"] = die
            state["last"] = {"seat": seat, "die": die, "moved": None}
        return state, None
    token = move.get("move")
    if not isinstance(token, int) or isinstance(token, bool) or token not in movable(state, seat, state["die"]):
        return state, "Pick a token that can move."
    die = state["die"]
    tokens = state["tokens"][seat]
    pos = tokens[token]
    new = 0 if pos == -1 else pos + die
    tokens[token] = new
    captured = []
    square = _square(state, seat, new)
    if square is not None and square not in SAFE:
        for other in range(state["players"]):
            if other == seat:
                continue
            for j, p in enumerate(state["tokens"][other]):
                if _square(state, other, p) == square:
                    state["tokens"][other][j] = -1
                    captured.append([other, j])
    state["last"] = {"seat": seat, "die": die, "moved": token, "captured": captured}
    if all(p == HOME for p in tokens):
        state["end"] = {"winner": seat, "reason": "home"}
        state["die"] = None
        return state, None
    if die == 6 or captured:
        state["die"] = None  # roll again
    else:
        _pass(state, seat)
    return state, None


def result(state: dict) -> dict | None:
    return state["end"]


def view(state: dict, seat: int) -> dict:
    mine = not state["end"] and state["turn"] == seat
    return {
        "tokens": state["tokens"], "players": state["players"], "offsets": state["offsets"], "turn": None if state["end"] else state["turn"],
        "die": state["die"], "last": state["last"],
        "movable": movable(state, seat, state["die"]) if mine and state["die"] else [],
        "can_roll": mine and state["die"] is None,
    }


def bot_move(state: dict, seat: int, level: str):
    if state["die"] is None:
        return {"roll": True}
    options = movable(state, seat, state["die"])
    if not options:
        return None
    if level == "easy":
        return {"move": random.choice(options)}

    def score(token: int) -> int:
        pos = state["tokens"][seat][token]
        new = 0 if pos == -1 else pos + state["die"]
        points = new  # further along is better
        if new == HOME:
            points += 100
        square = _square(state, seat, new)
        if square is not None and square not in SAFE:
            for other in range(state["players"]):
                if other != seat and any(_square(state, other, p) == square for p in state["tokens"][other]):
                    points += 60  # captures
        if pos == -1:
            points += 40  # bring one out
        if square in SAFE:
            points += 8
        return points

    return {"move": max(options, key=score)}
