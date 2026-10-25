"""What a game's rules module gives the rooms core (Referee mode). The core owns rooms, codes, sign-in, reconnecting, limits
and bots' turns; a module owns only the rules. Snapshots are per player (`view`), so card games can hide hands.

A module is any object with these attributes (a class with the methods as plain functions works):

    name           the game's name on start.ng (its address, <name>.start.ng; the room's ticket is for this game)
    min_players    a room starts when it has this many (and is full at max_players)
    max_players
    new_state(players: int) -> state              the starting state (JSON-able)
    turn(state) -> int | None                     whose seat moves next (None: not turn based, or it's over)
    apply(state, seat: int, move) -> (state, error)   error is a short sentence for the player, or None when the move was legal
    result(state) -> dict | None                  None while playing; else {"winner": seat | None, "reason": "..."}
    view(state, seat: int) -> dict                what that player may see
    bot_move(state, seat: int, level: str) -> move | None
"""
from typing import Any, Protocol


class GameRules(Protocol):
    name: str
    min_players: int
    max_players: int

    def new_state(self, players: int) -> Any: ...
    def turn(self, state: Any) -> int | None: ...
    def apply(self, state: Any, seat: int, move: Any) -> tuple[Any, str | None]: ...
    def result(self, state: Any) -> dict | None: ...
    def view(self, state: Any, seat: int) -> dict: ...
    def bot_move(self, state: Any, seat: int, level: str) -> Any: ...
