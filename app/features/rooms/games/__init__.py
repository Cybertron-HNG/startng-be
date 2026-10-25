"""The games the rooms core can referee. Add a module here (see base.py) and list it below."""
from . import connect4, draughts, tictactoe

GAMES = {m.name: m for m in (tictactoe, connect4, draughts)}
