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
    def put_file(self, game: str, path: str, data: bytes, content_type: str) -> None: ...
    def delete_file(self, game: str, path: str) -> None: ...
    def delete_game(self, game: str) -> None: ...
    def set_hidden(self, game: str, hidden: bool) -> None: ...  # take the game offline (the Worker shows an error page)
    def read_file(self, game: str, path: str) -> bytes | None: ...  # one file of a game, or None if it isn't there
    def file_sizes(self, game: str) -> dict[str, int]: ...  # every file of a game and its size in bytes


class R2GameStore:
    def paths(self, game: str) -> list[str]:
        return [f["path"] for f in storage.list_files(game)]

    def put_game(self, game: str, files: dict[str, bytes]) -> None:
        storage.put_game(game, files)

    def put_file(self, game: str, path: str, data: bytes, content_type: str) -> None:
        storage.put_file(game, path, data, content_type)

    def delete_file(self, game: str, path: str) -> None:
        storage.delete_file(game, path)

    def delete_game(self, game: str) -> None:
        storage.delete_game(game)

    def set_hidden(self, game: str, hidden: bool) -> None:
        storage.set_hidden(game, hidden)

    def file_sizes(self, game: str) -> dict[str, int]:
        return {f["path"]: f["size"] for f in storage.list_files(game)}

    def read_file(self, game: str, path: str) -> bytes | None:
        try:
            return storage.get_file(game, path)
        except Exception:  # not there (or R2 hiccup): callers treat it as missing
            return None


class LocalGameStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _dir(self, game: str) -> Path:
        folder = (self.root / storage.validate_game_name(game)).resolve()
        if not folder.is_relative_to(self.root):
            raise storage.StorageError("Invalid game name")
        return folder

    def _file(self, game: str, path: str) -> Path:
        """A file inside the game's folder; anything that resolves outside it is refused."""
        folder = self._dir(game)
        target = (folder / storage.clean_path(path)).resolve()
        if not target.is_relative_to(folder):
            raise storage.StorageError("Invalid file path")
        return target

    def paths(self, game: str) -> list[str]:
        folder = self._dir(game)
        if not folder.exists():
            return []
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())

    def put_game(self, game: str, files: dict[str, bytes]) -> None:
        folder = self._dir(game)
        cleaned = {storage.clean_path(p): data for p, data in files.items()}  # check all before writing
        targets = {path: self._file(game, path) for path in cleaned}
        for path, data in cleaned.items():
            target = targets[path]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def put_file(self, game: str, path: str, data: bytes, content_type: str) -> None:
        self.put_game(game, {path: data})

    def delete_file(self, game: str, path: str) -> None:
        self._file(game, path).unlink(missing_ok=True)

    def delete_game(self, game: str) -> None:
        shutil.rmtree(self._dir(game), ignore_errors=True)
        self.set_hidden(game, False)

    def set_hidden(self, game: str, hidden: bool) -> None:
        marker = self.root / storage.HIDDEN_PREFIX / storage.validate_game_name(game)
        if hidden:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_bytes(b"")
        else:
            marker.unlink(missing_ok=True)

    def file_sizes(self, game: str) -> dict[str, int]:
        folder = self._dir(game)
        return {p.relative_to(folder).as_posix(): p.stat().st_size for p in folder.rglob("*") if p.is_file()} if folder.exists() else {}

    def read_file(self, game: str, path: str) -> bytes | None:
        target = self._file(game, path)
        return target.read_bytes() if target.is_file() else None


def get_game_store() -> GameStore | None:
    """None when publishing isn't set up (no R2 keys and no LOCAL_GAMES_DIR)."""
    if settings.r2_account_id and settings.r2_access_key_id and settings.r2_secret_access_key:
        return R2GameStore()
    if settings.local_games_dir:
        return LocalGameStore(settings.local_games_dir)
    return None
