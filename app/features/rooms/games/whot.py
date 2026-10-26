"""Whot, the Nigerian card game, for 2 to 4 players, refereed by the server (it deals, shuffles and checks every card, and each player
only ever receives their own hand).

Rules used (also written on the game's page):
- 54 cards: circles and triangles (1 to 5, 7, 8, 10 to 14), crosses and squares (1, 2, 3, 5, 7, 10, 11, 13, 14), stars (1 to 5, 7, 8) and five Whot
  cards (number 20). Everyone is dealt 6 cards with two players, 5 with more. One card starts the pile.
- On your turn you play a card that matches the top one in shape or in number, or a Whot card on anything, or you pick one card from the market and
  your turn ends. The first to play their last card wins.
- A Whot card lets you name the shape the next player must follow.
- 1 (hold on): you play again. 14 (general market): everyone else picks one card and you play again. 8 (suspension): the next player misses
  their turn. 2 (pick two) and 5 (pick three): the next player picks 2 or 3 cards, unless they play another 2 (or 5), which adds on and passes the pick to
  the player after them. Whoever cannot, or does not want to, defend picks all that has built up.
- When the market runs out, the pile (except its top card) is shuffled and becomes the market.

A move is {"play": <the card's place in your hand>, "shape": "circle" | "triangle" | "cross" | "square" | "star"} (the shape only with a Whot card),
or {"draw": true}. State is per player (`view`): your hand, how many cards each player holds, the top card, the shape to follow, what is waiting to be
picked up, which of your cards can be played, and the market's size.
"""
import random

name = "whot"
start_mode = "start"  # the creator starts it when everyone is in; bots can fill seats
min_players = 2
max_players = 4
forfeit = False

SHAPES = ["circle", "triangle", "cross", "square", "star"]
NUMBERS = {
    "circle": [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 14], "triangle": [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 14],
    "cross": [1, 2, 3, 5, 7, 10, 11, 13, 14], "square": [1, 2, 3, 5, 7, 10, 11, 13, 14], "star": [1, 2, 3, 4, 5, 7, 8],
}
SPECIAL = {1, 2, 5, 8, 14, 20}
PICK = {2: 2, 5: 3}


def _deck() -> list[list]:
    cards = [[shape, n] for shape in SHAPES for n in NUMBERS[shape]]
    return cards + [["whot", 20]] * 5


def new_state(players: int, options=None) -> dict:
    rng = random.Random()
    market = _deck()
    rng.shuffle(market)
    first = next(c for c in market if c[1] not in SPECIAL)  # the starting card is a plain one
    market.remove(first)
    each = 6 if players == 2 else 5
    hands = {seat: [market.pop() for _ in range(each)] for seat in range(players)}
    return {"market": market, "discard": [first], "hands": hands, "players": players, "turn": 0, "shape": first[0], "pending": None,
            "last": None, "end": None, "rng": rng}


def _refill(state: dict) -> None:
    if not state["market"] and len(state["discard"]) > 1:
        top = state["discard"].pop()
        state["market"] = state["discard"]
        state["rng"].shuffle(state["market"])
        state["discard"] = [top]


def _draw(state: dict, seat: int, count: int) -> int:
    drawn = 0
    for _ in range(count):
        _refill(state)
        if not state["market"]:
            break
        state["hands"][seat].append(state["market"].pop())
        drawn += 1
    return drawn


def _playable(state: dict, card: list) -> bool:
    pending = state["pending"]
    if pending:
        return card[1] == pending["kind"]  # only another 2 (or 5) defends
    if card[0] == "whot":
        return True
    top = state["discard"][-1]
    return card[0] == state["shape"] or (top[0] != "whot" and card[1] == top[1])


def playable(state: dict, seat: int) -> list[int]:
    return [i for i, card in enumerate(state["hands"][seat]) if _playable(state, card)]


def _next(state: dict, seat: int, steps: int = 1) -> int:
    return (seat + steps) % state["players"]


def turn(state: dict) -> int | None:
    return None if state["end"] else state["turn"]


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if state["end"]:
        return state, "The game is over."
    if seat != state["turn"]:
        return state, "It's not your turn."
    if not isinstance(move, dict):
        return state, "Play a card or pick from the market."
    if move.get("draw"):
        pending = state["pending"]
        drawn = _draw(state, seat, PICK[pending["kind"]] * pending["count"] if pending else 1)
        state["pending"] = None
        state["last"] = {"seat": seat, "drew": drawn}
        state["turn"] = _next(state, seat)
        return state, None
    index = move.get("play")
    hand = state["hands"][seat]
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(hand):
        return state, "Pick a card from your hand."
    card = hand[index]
    if not _playable(state, card):
        if state["pending"]:
            return state, f"You have to play a {state['pending']['kind']} or pick up."
        return state, "That card does not match the shape or the number."
    shape = move.get("shape")
    if card[0] == "whot" and shape not in SHAPES:
        return state, "Say which shape you want."
    hand.pop(index)
    state["discard"].append(card)
    state["shape"] = shape if card[0] == "whot" else card[0]
    state["last"] = {"seat": seat, "card": card, "shape": state["shape"]}
    if not hand:
        state["end"] = {"winner": seat, "reason": "emptied"}
        return state, None
    number = card[1]
    if number in PICK:
        state["pending"] = {"kind": number, "count": (state["pending"]["count"] if state["pending"] else 0) + 1}
        state["turn"] = _next(state, seat)
    elif number == 1:
        pass  # hold on: the same player goes again
    elif number == 14:
        for other in range(state["players"]):
            if other != seat:
                _draw(state, other, 1)
    elif number == 8:
        state["turn"] = _next(state, seat, 2)
    else:
        state["turn"] = _next(state, seat)
    return state, None


def result(state: dict) -> dict | None:
    return state["end"]


def view(state: dict, seat: int) -> dict:
    pending = state["pending"]
    out = {
        "hand": state["hands"].get(seat, []) if seat in state["hands"] else [],
        "counts": [len(state["hands"][s]) for s in range(state["players"])],
        "top": state["discard"][-1], "shape": state["shape"], "market": len(state["market"]),
        "pending": {"kind": pending["kind"], "cards": PICK[pending["kind"]] * pending["count"]} if pending else None,
        "turn": None if state["end"] else state["turn"], "last": state["last"],
        "playable": playable(state, seat) if not state["end"] and state["turn"] == seat else [],
    }
    if state["end"]:
        out["hands"] = [state["hands"][s] for s in range(state["players"])]
    return out


def bot_move(state: dict, seat: int, level: str):
    options = playable(state, seat)
    if not options:
        return {"draw": True}
    hand = state["hands"][seat]
    plain = [i for i in options if hand[i][0] != "whot"]
    index = random.choice(plain or options) if level == "easy" else (plain or options)[0] if state["pending"] else random.choice(plain or options)
    move = {"play": index}
    if hand[index][0] == "whot":
        counts = {s: sum(1 for c in hand if c[0] == s) for s in SHAPES}
        move["shape"] = max(SHAPES, key=lambda s: counts[s])
    return move
