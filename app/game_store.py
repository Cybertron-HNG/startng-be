"""Where POST /games/publish puts games.

On the server this is the R2 games bucket, through app.storage. Without the R2 keys, and with
LOCAL_GAMES_DIR set, it's a local folder laid out the same way (the Worker's dev-server.js serves
it), so publishing can be tried on a laptop. Name and path rules are app.storage's either way.
"""
import shutil
from pathlib import Path
from typing import Protocol

from . import storage
from .config import settings


class GameStore(Protocol):
    def paths(self, game: str) -> list[str]: ...
    def put_game(self, game: str, files: dict[str, bytes]) -> None: ...
    def delete_game(self, game: str) -> None: ...


class R2GameStore:
    def paths(self, game: str) -> list[str]:
        return [f["path"] for f in storage.list_files(game)]

    def put_game(self, game: str, files: dict[str, bytes]) -> None:
        storage.put_game(game, files)

    def delete_game(self, game: str) -> None:
        storage.delete_game(game)


class LocalGameStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _dir(self, game: str) -> Path:
        return self.root / storage.validate_game_name(game)

    def paths(self, game: str) -> list[str]:
        folder = self._dir(game)
        if not folder.exists():
            return []
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())

    def put_game(self, game: str, files: dict[str, bytes]) -> None:
        folder = self._dir(game)
        cleaned = {storage.clean_path(p): data for p, data in files.items()}  # check all before writing
        for path, data in cleaned.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def delete_game(self, game: str) -> None:
        shutil.rmtree(self._dir(game), ignore_errors=True)


def get_game_store() -> GameStore | None:
    """None when publishing isn't set up (no R2 keys and no LOCAL_GAMES_DIR)."""
    if settings.r2_account_id and settings.r2_access_key_id and settings.r2_secret_access_key:
        return R2GameStore()
    if settings.local_games_dir:
        return LocalGameStore(settings.local_games_dir)
    return None
