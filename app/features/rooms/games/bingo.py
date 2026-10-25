"""Bingo for a room of up to 100 people. The person who makes the room is the host (seat 0): they pick the kind of game and start it.
Everyone else gets a private card; the server draws, one item every few seconds, and checks every claim.

Setup (sent by the host before the start): {"mode": "numbers" | "terms", "terms": ["...", ...], "speed": 8, "pattern": "line" | "full"}
- numbers: the classic 1 to 75 (B 1-15, I 16-30, N 31-45, G 46-60, O 61-75); the centre square is free.
- terms: your own words (24 to 100 of them), for a class or a party; 24 are picked for each card; the centre is free.
- speed: seconds between draws, 3 to 60 (8 if left out). pattern: a line (across, down or corner to corner) or the full card (line if left out).

Moves: a player sends {"mark": 0-24} to mark or unmark a square that has been called (the free centre, 12, is always marked) and
{"bingo": true} when they think they have the pattern; a claim that is not right costs 10 seconds before the next one.
The host sends {"draw": true} to draw the next one now, and {"pause": true | false}.

The first right claim wins and ends the game; if everything has been drawn and nobody claimed, it ends with no winner.
"""
import random
import time

name = "bingo"
start_mode = "host"
min_players = 2  # the host and one player
max_players = 101  # the host and 100 players
forfeit = False

READY_MS = 3000
CLAIM_PENALTY_MS = 10_000
CENTER = 12
LETTERS = "BINGO"
LINES = [list(range(r * 5, r * 5 + 5)) for r in range(5)] + [list(range(c, 25, 5)) for c in range(5)] + [[0, 6, 12, 18, 24], [4, 8, 12, 16, 20]]
FULL = [list(range(25))]


def now_ms() -> int:
    return int(time.time() * 1000)  # tests replace this


def validate_setup(data) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "Send the bingo's settings."
    mode = data.get("mode", "numbers")
    if mode not in ("numbers", "terms"):
        return None, "Pick numbers or your own words."
    speed = data.get("speed", 8)
    if not isinstance(speed, int) or isinstance(speed, bool) or not 3 <= speed <= 60:
        return None, "Seconds between draws must be 3 to 60."
    pattern = data.get("pattern", "line")
    if pattern not in ("line", "full"):
        return None, "Pick a line or the full card."
    options = {"mode": mode, "speed": speed, "pattern": pattern}
    if mode == "numbers":
        options["items"] = [f"{LETTERS[(n - 1) // 15]} {n}" for n in range(1, 76)]
        return options, None
    raw = data.get("terms")
    if not isinstance(raw, list):
        return None, "Add your words."
    terms = []
    for item in raw:
        text = item.strip()[:40] if isinstance(item, str) else ""
        if text and text.lower() not in {t.lower() for t in terms}:
            terms.append(text)
    if not 24 <= len(terms) <= 100:
        return None, f"Bingo needs 24 to 100 different words (you have {len(terms)})."
    options["items"] = terms
    return options, None


def _deal(state: dict) -> list[int | None]:
    items, rng = state["items"], state["rng"]
    if state["mode"] == "numbers":
        columns = [rng.sample(range(c * 15, c * 15 + 15), 5) for c in range(5)]
        card = [columns[c][r] for r in range(5) for c in range(5)]
    else:
        pick = rng.sample(range(len(items)), 24)
        card = pick[:CENTER] + [None] + pick[CENTER:]
    card[CENTER] = None
    return card


def new_state(players: int, options: dict | None = None) -> dict:
    if not options:
        raise ValueError("Pick the kind of bingo first.")
    rng = random.Random()
    state = {
        "mode": options["mode"], "items": options["items"], "speed": options["speed"], "pattern": options["pattern"],
        "rng": rng, "order": rng.sample(range(len(options["items"])), len(options["items"])), "called": [],
        "cards": {}, "marks": {}, "penalty": {}, "phase": "playing", "paused": False, "winner": None, "line": None,
        "next_draw_ms": now_ms() + READY_MS,
    }
    for seat in range(1, players):
        add_player(state, seat)
    return state


def add_player(state: dict, seat: int) -> dict:
    if seat not in state["cards"]:
        state["cards"][seat] = _deal(state)
        state["marks"][seat] = set()
    return state


def _draw(state: dict, now: int) -> bool:
    if len(state["called"]) >= len(state["order"]):
        return False
    state["called"].append(state["order"][len(state["called"])])
    state["next_draw_ms"] = now + state["speed"] * 1000
    return True


def tick(state: dict, now: int) -> tuple[dict, bool]:
    if state["phase"] != "playing" or state["paused"] or now < state["next_draw_ms"]:
        return state, False
    if not _draw(state, now):
        state["phase"] = "over"
    return state, True


def turn(state: dict) -> int | None:
    return None


def _winning_line(state: dict, seat: int) -> list[int] | None:
    called = set(state["called"])
    card, marks = state["cards"][seat], state["marks"][seat]
    ok = [i == CENTER or (i in marks and card[i] in called) for i in range(25)]
    for line in (FULL if state["pattern"] == "full" else LINES):
        if all(ok[i] for i in line):
            return line
    return None


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if state["phase"] == "over":
        return state, "The game is over."
    if not isinstance(move, dict):
        return state, "Send a move."
    now = now_ms()
    if seat == 0:
        if move.get("draw"):
            if not _draw(state, now):
                return state, "Everything has been drawn."
        elif "pause" in move:
            state["paused"] = bool(move["pause"])
            if not state["paused"]:
                state["next_draw_ms"] = now + state["speed"] * 1000
        else:
            return state, "The host can draw or pause."
        return state, None
    if seat not in state["cards"]:
        return state, "You are not playing."
    if "mark" in move:
        square = move["mark"]
        if not isinstance(square, int) or isinstance(square, bool) or not 0 <= square < 25 or square == CENTER:
            return state, "Pick a square on your card."
        marks = state["marks"][seat]
        if square in marks:
            marks.discard(square)
        elif state["cards"][seat][square] in state["called"]:
            marks.add(square)
        else:
            return state, "That has not been called yet."
        return state, None
    if move.get("bingo"):
        if now < state["penalty"].get(seat, 0):
            return state, "Wait a few seconds before you try again."
        line = _winning_line(state, seat)
        if line is None:
            state["penalty"][seat] = now + CLAIM_PENALTY_MS
            return state, "Not yet: that isn't a bingo. Wait 10 seconds to try again."
        state.update(phase="over", winner=seat, line=line)
        return state, None
    return state, "Mark a square, or call bingo."


def result(state: dict) -> dict | None:
    if state["phase"] != "over":
        return None
    if state["winner"] is None:
        return {"winner": None, "reason": "all_drawn"}
    return {"winner": state["winner"], "reason": "bingo", "line": state["line"],
            "card": [state["items"][i] if i is not None else None for i in state["cards"][state["winner"]]]}


def view(state: dict, seat: int) -> dict:
    items = state["items"]
    called = state["called"]
    out = {
        "mode": state["mode"], "pattern": state["pattern"], "speed": state["speed"], "paused": state["paused"],
        "phase": state["phase"], "host": seat == 0, "players": len(state["cards"]),
        "called": [items[i] for i in called], "last": items[called[-1]] if called else None,
        "left": len(state["order"]) - len(called), "next_draw_ms": state["next_draw_ms"],
    }
    if seat in state["cards"]:
        marks = state["marks"][seat]
        out["card"] = [{"label": items[v] if v is not None else "Free", "marked": i == CENTER or i in marks, "called": v is None or v in called}
                       for i, v in enumerate(state["cards"][seat])]
    return out


def bot_move(state: dict, seat: int, level: str):
    return None
