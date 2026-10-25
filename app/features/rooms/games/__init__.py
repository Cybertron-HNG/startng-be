"""The games the rooms core can referee. Add a module here (see base.py) and list it below."""
from . import bingo, connect4, draughts, quiz, tictactoe

GAMES = {m.name: m for m in (tictactoe, connect4, draughts, quiz, bingo)}
