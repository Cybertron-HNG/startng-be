"""startng.json, a section at a time.

A game's startng.json is one object; each top-level key is a section owned by one part of start.ng:

    {"leaderboard": {...}, "description": "...", "category": "Puzzle"}

Each owner registers its section here, with a parse function that checks it and an optional apply function that
saves it when the game is published or updated. Unknown keys are ignored, so a game can carry sections a
feature hasn't shipped yet.

    from app import manifest

    def parse_category(value):              # raise manifest.SchemaError("...") when it's wrong
        ...
        return "Puzzle"

    def save_category(db, game, value):     # optional: runs before the publish is committed
        ...

    manifest.register_section("category", parse_category, save_category)

A SchemaError refuses the publish with a clear message, like a bad leaderboard does today. If a section is
optional and shouldn't block publishing, catch problems in parse and return None instead.
"""
import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from .leaderboard import SchemaError, clean_description, parse_leaderboard

__all__ = ["SchemaError", "register_section", "parse_sections", "apply_sections", "SECTIONS"]


@dataclass
class Section:
    key: str
    parse: Callable[[Any], Any]
    apply: Callable[[Session, Any, Any], None] | None = None  # (db, game, parsed value)


SECTIONS: dict[str, Section] = {}


def register_section(key: str, parse: Callable[[Any], Any], apply: Callable[[Session, Any, Any], None] | None = None) -> None:
    if key in SECTIONS and SECTIONS[key].parse is not parse:
        raise ValueError(f'startng.json section "{key}" is already owned by another part of start.ng')
    SECTIONS[key] = Section(key, parse, apply)


def parse_sections(raw: bytes) -> dict[str, Any]:
    """Every registered section that the file has, parsed. Raises SchemaError for broken JSON or a bad section."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise SchemaError("startng.json isn't valid JSON") from err
    if not isinstance(data, dict):
        return {}
    return {key: s.parse(data[key]) for key, s in SECTIONS.items() if key in data}


def apply_sections(db: Session, game: Any, values: dict[str, Any]) -> None:
    """Let each section save its value for a game being published or updated (before the publish commits)."""
    for key, s in SECTIONS.items():
        if s.apply is not None and key in values:
            s.apply(db, game, values[key])


# ---- start.ng's own sections (the leaderboard and the description are saved by the publish code itself)
register_section("leaderboard", parse_leaderboard)
register_section("description", lambda value: clean_description(value) if isinstance(value, str) else None)
