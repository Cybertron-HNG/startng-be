"""The games the rooms core can referee. Add a module here (see base.py) and list it below."""
from . import tictactoe

GAMES = {m.name: m for m in (tictactoe,)}
