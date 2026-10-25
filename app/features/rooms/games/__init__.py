"""The games the rooms core can referee. Add a module here (see base.py) and list it below."""
from . import ayo, bingo, connect4, draughts, ludo, quiz, tictactoe, whot

GAMES = {m.name: m for m in (tictactoe, connect4, draughts, ayo, whot, ludo, quiz, bingo)}
