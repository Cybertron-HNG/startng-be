"""Screenshots. What other features may call (the contract):

    from app.features.screenshots import service as screenshots

    screenshots.screenshots_of(db, "lagos-run")   # [{"url": "https://lagos-run.start.ng/shots/1.png", "alt": "..."}]

A game lists its pictures in startng.json, as files in its own folder:

    {"screenshots": ["shots/level-1.png", "shots/boss.webp"]}

Up to MAX_SHOTS, each a .png, .jpg, .jpeg or .webp, no hidden files or "..". Saved when the game is published or updated;
"screenshots": [] removes them. An unknown or broken list refuses the publish with a clear message, like a bad leaderboard.
"""
from sqlalchemy.orm import Session

from ... import flags, manifest
from ...storage import StorageError, clean_path, game_url
from .models import GameScreenshots

NAME = "screenshots"
MAX_SHOTS = 4
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp")


def _parse(value) -> list[str]:
    if not isinstance(value, list):
        raise manifest.SchemaError('screenshots must be a list of image files in your game, e.g. ["shots/level-1.png"]')
    if len(value) > MAX_SHOTS:
        raise manifest.SchemaError(f"screenshots can list at most {MAX_SHOTS} images")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise manifest.SchemaError("each screenshot must be the path of an image file in your game")
        try:
            path = clean_path(item.strip())
        except StorageError:
            raise manifest.SchemaError(f'"{item}" isn\'t a usable path (no "..", backslashes or hidden files)')
        if not path.lower().endswith(IMAGE_TYPES):
            raise manifest.SchemaError(f'"{item}" must be a .png, .jpg, .jpeg or .webp image')
        if path not in paths:
            paths.append(path)
    return paths


def _save(db: Session, game, paths: list[str]) -> None:
    row = db.get(GameScreenshots, game.name)
    if row is None:
        db.add(GameScreenshots(game_name=game.name, paths=paths))
    else:
        row.paths = paths


manifest.register_section("screenshots", _parse, _save)


def screenshots_of(db: Session, name: str) -> list[dict]:
    """The game's screenshots as full addresses with alt text, in the order its startng.json lists them. [] while the
    feature is off or the game lists none."""
    if not flags.is_on(NAME):
        return []
    row = db.get(GameScreenshots, name)
    title = " ".join(w.capitalize() for w in name.split("-"))
    return [{"url": f"{game_url(name)}/{path}", "alt": f"{title} screenshot {i}"} for i, path in enumerate(row.paths if row else [], 1)]
