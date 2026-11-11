"""Read/write access to the start.ng games bucket (Cloudflare R2, S3 API).

Layout matches the Worker in startng-fe/worker: a game called `dave` lives under the
`dave/` prefix and is served at https://dave.start.ng. Names and paths are validated here
with the same rules the Worker applies, so nothing is written that it would refuse to serve.
"""
import mimetypes
import re
from functools import lru_cache
from typing import Iterable

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings

# Same rule as the Worker: lowercase letters, digits, hyphens, no leading/trailing hyphen.
GAME_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# Content types the Worker forces by extension; anything else falls back to mimetypes.
_TYPES = {
    "html": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "svg": "image/svg+xml",
    "wasm": "application/wasm",
}


class StorageError(Exception):
    """Raised for invalid names/paths or when R2 is not configured."""


class GameNotFound(StorageError):
    pass


def reserved_names() -> set[str]:
    return {n.strip().lower() for n in settings.reserved_game_names.split(",") if n.strip()}


def validate_game_name(name: str) -> str:
    name = name.strip().lower()
    if not GAME_NAME_RE.match(name):
        raise StorageError("Game name must be lowercase letters, digits and hyphens")
    if name in reserved_names():
        raise StorageError(f"'{name}' is reserved")
    return name


def clean_path(path: str) -> str:
    """Normalise a file path inside a game; reject traversal, hidden files, backslashes."""
    if "\\" in path or "\0" in path:
        raise StorageError("Invalid file path")
    segments = [s for s in path.split("/") if s]
    if not segments or any(s.startswith(".") for s in segments):
        raise StorageError("Invalid file path")
    return "/".join(segments)


def content_type_for(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def game_url(name: str) -> str:
    if name == "hangout":
        return f"{settings.site_url.rstrip('/')}/hangout"
    return f"https://{name}.{settings.games_base_domain}"


@lru_cache
def _client():
    if not (settings.r2_account_id and settings.r2_access_key_id and settings.r2_secret_access_key):
        raise StorageError("R2 storage is not configured")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def put_file(game: str, path: str, data: bytes, content_type: str | None = None) -> str:
    """Write one file. Returns the bucket key."""
    key = f"{validate_game_name(game)}/{clean_path(path)}"
    _client().put_object(
        Bucket=settings.r2_bucket,
        Key=key,
        Body=data,
        ContentType=content_type or content_type_for(key),
    )
    return key


def put_game(game: str, files: dict[str, bytes]) -> list[str]:
    """Write many files ({relative_path: bytes}) into one game. Returns the keys written."""
    validate_game_name(game)
    cleaned = {clean_path(p): d for p, d in files.items()}  # validate all before writing any
    return [put_file(game, p, d) for p, d in cleaned.items()]


def get_file(game: str, path: str) -> bytes:
    key = f"{validate_game_name(game)}/{clean_path(path)}"
    try:
        return _client().get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise GameNotFound(key) from e
        raise


def list_files(game: str) -> list[dict]:
    """[{path, size, last_modified}] for every file in a game."""
    prefix = f"{validate_game_name(game)}/"
    out = []
    for page in _client().get_paginator("list_objects_v2").paginate(
        Bucket=settings.r2_bucket, Prefix=prefix
    ):
        for o in page.get("Contents", []):
            out.append(
                {"path": o["Key"][len(prefix):], "size": o["Size"], "last_modified": o["LastModified"]}
            )
    return out


def list_games() -> list[str]:
    """Names of all games (top-level folders) in the bucket."""
    names: list[str] = []
    for page in _client().get_paginator("list_objects_v2").paginate(
        Bucket=settings.r2_bucket, Delimiter="/"
    ):
        names += [p["Prefix"].rstrip("/") for p in page.get("CommonPrefixes", [])]
    return [n for n in names if not n.startswith("_")]  # _hidden/ holds markers, not a game


def delete_game(game: str) -> int:
    """Delete every file of a game. Returns how many objects were removed."""
    prefix = f"{validate_game_name(game)}/"
    removed = 0
    for page in _client().get_paginator("list_objects_v2").paginate(
        Bucket=settings.r2_bucket, Prefix=prefix
    ):
        keys: Iterable[dict] = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            _client().delete_objects(Bucket=settings.r2_bucket, Delete={"Objects": list(keys)})
            removed += len(keys)
    _client().delete_object(Bucket=settings.r2_bucket, Key=HIDDEN_PREFIX + game.strip().lower())  # its marker, if hidden
    return removed


# A hidden game (app/admin.py) has an empty object at _hidden/<name>; the Worker then serves an error page instead of
# the game. It sits outside every game's folder, so no game can write or serve it ("_" can't start a game name).
HIDDEN_PREFIX = "_hidden/"


def set_hidden(game: str, hidden: bool) -> None:
    key = HIDDEN_PREFIX + validate_game_name(game)
    if hidden:
        _client().put_object(Bucket=settings.r2_bucket, Key=key, Body=b"", ContentType="text/plain")
    else:
        _client().delete_object(Bucket=settings.r2_bucket, Key=key)


def delete_file(game: str, path: str) -> None:
    """Delete one file of a game (no error if it isn't there)."""
    key = f"{validate_game_name(game)}/{clean_path(path)}"
    _client().delete_object(Bucket=settings.r2_bucket, Key=key)
