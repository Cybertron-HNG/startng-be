"""Game-defined leaderboards.

A game opts in with a startng.json in its folder (or at the top of its repo):

    {"leaderboard": {"stats": [
        {"key": "stage", "label": "Stage", "order": "highest"},
        {"key": "kills", "label": "Enemies killed", "order": "highest"},
        {"key": "time", "label": "Time", "order": "lowest", "format": "time"}
    ]}}

Players are ranked by the first stat, then the next ones break ties, then whoever got there
first. Only whole numbers for the declared stats are stored: no text, so nothing a player
sends can show up as words or links on start.ng.

Games without a startng.json keep the plain board: one "score", highest first.
"""
import json

import re

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
MAX_STATS = 5
MAX_LABEL = 30
MAX_VALUE = 1_000_000_000
ORDERS = ("highest", "lowest")
FORMATS = ("number", "time")  # time = seconds, shown as m:ss
MANIFEST = "startng.json"
DEFAULT = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]


class SchemaError(Exception):
    """A problem with a game's startng.json. The message is shown to the builder."""


def parse_manifest(raw: bytes) -> list[dict] | None:
    """The leaderboard stats from a startng.json, or None when the game doesn't ask for one."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise SchemaError("startng.json isn't valid JSON") from err
    if not isinstance(data, dict) or "leaderboard" not in data:
        return None
    return parse_leaderboard(data["leaderboard"])


def parse_leaderboard(board) -> list[dict]:
    """The "leaderboard" section of a startng.json (app/manifest.py), checked."""
    stats = board.get("stats") if isinstance(board, dict) else None
    if not isinstance(stats, list) or not 1 <= len(stats) <= MAX_STATS:
        raise SchemaError(f"leaderboard.stats must list 1 to {MAX_STATS} stats")
    out, seen = [], set()
    for s in stats:
        if not isinstance(s, dict):
            raise SchemaError("each stat must be an object with a key and a label")
        key, label = s.get("key"), s.get("label")
        order, fmt = s.get("order", "highest"), s.get("format", "number")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise SchemaError(f'stat key "{key}" must be lowercase letters, digits or _ (max 24)')
        if key in seen:
            raise SchemaError(f'stat key "{key}" is listed twice')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= MAX_LABEL:
            raise SchemaError(f'stat "{key}" needs a label of 1 to {MAX_LABEL} characters')
        if order not in ORDERS:
            raise SchemaError(f'stat "{key}": order must be "highest" or "lowest"')
        if fmt not in FORMATS:
            raise SchemaError(f'stat "{key}": format must be "number" or "time"')
        seen.add(key)
        out.append({"key": key, "label": label.strip(), "order": order, "format": fmt})
    return out


def check_stats(schema: list[dict], stats: dict) -> dict[str, int]:
    """Exactly the declared stats, each a whole number from 0 to MAX_VALUE."""
    keys = [s["key"] for s in schema]
    if not isinstance(stats, dict) or set(stats) != set(keys):
        raise ValueError(f"send exactly these stats: {', '.join(keys)}")
    out = {}
    for k in keys:
        v = stats[k]
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_VALUE:
            raise ValueError(f'"{k}" must be a whole number from 0 to {MAX_VALUE:,}')
        out[k] = v
    return out


def stats_of(schema: list[dict], stored: dict | None, best: int) -> dict[str, int | None]:
    """A row's stats for this schema (rows from before a schema change may miss some)."""
    stored = stored if stored is not None else {"score": best}
    return {s["key"]: stored.get(s["key"]) for s in schema}


def rank_key(schema: list[dict], stats: dict) -> tuple:
    """Sorts best first. A missing stat counts as the worst value."""
    key = []
    for s in schema:
        v = stats.get(s["key"])
        if v is None:
            key.append(float("inf"))
        else:
            key.append(-v if s["order"] == "highest" else v)
    return tuple(key)


def is_better(schema: list[dict], new: dict, old: dict) -> bool:
    return rank_key(schema, new) < rank_key(schema, old)


DESCRIPTION_MAX = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_description(text: str) -> str | None:
    """Plain, single-line text cut to DESCRIPTION_MAX, or None when nothing is left."""
    text = " ".join(_CONTROL.sub(" ", text).split())
    return text[:DESCRIPTION_MAX] or None


def parse_description(raw: bytes) -> str | None:
    """The game's one-line description from its startng.json ({"description": "..."}), cleaned up, or None.

    Never an error: a missing or odd description just means the page finds or writes its own. It's plain
    text (the website escapes it), cut to DESCRIPTION_MAX characters.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    text = data.get("description") if isinstance(data, dict) else None
    return clean_description(text) if isinstance(text, str) else None
