"""Turn a public GitHub repo into a game's files, ready to upload.

Only client-side games are accepted: the repo must already contain a ready-to-play
index.html. Nothing from the repo is ever run on the server.
"""
import html
import io
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass, field

import requests

from . import storage
from .checks import Problem, inspect
from .leaderboard import SchemaError, clean_description, parse_description, parse_manifest

MAX_NAME = 40  # the games table's limit; app.storage applies the Worker's name rules
REPO_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([\w./-]+?))?/?$")
BRANCH_RE = re.compile(r"^[\w.-]+(/[\w.-]+)*$")

# Where a ready-to-play index.html may be, in order (same as the Worker's publish-game.js).
GAME_DIRS = ["dist", "build", "docs", "out", ""]
MAX_FILES = 2000
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20000

# The file types the games Worker serves (its CONTENT_TYPES). Anything else is skipped.
MANIFEST = "startng.json"  # the game's start.ng settings (app/leaderboard.py)
MAX_MANIFEST_BYTES = 64 * 1024

SERVED = {
    "html", "htm", "css", "js", "mjs", "json", "map", "txt", "svg", "png", "jpg", "jpeg", "gif", "webp",
    "avif", "ico", "woff", "woff2", "ttf", "otf", "mp3", "ogg", "wav", "mp4", "webm", "wasm",
}


class PublishError(Exception):
    """A problem the builder can fix. The message is shown to them; the code is for scripts."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code
        self.warnings: list[Problem] = []

    @property
    def errors(self) -> list[Problem]:
        return [Problem(self.code, str(self))]


class GameRejected(PublishError):
    """The game was read but has problems: all of them, so they can be fixed in one go."""

    def __init__(self, errors: list[Problem], warnings: list[Problem]):
        more = f" (and {len(errors) - 1} more problem{'s' if len(errors) > 2 else ''})" if len(errors) > 1 else ""
        super().__init__(errors[0].message + more, errors[0].code)
        self._errors, self.warnings = errors, warnings

    @property
    def errors(self) -> list[Problem]:
        return self._errors


def _extension(path: str) -> str:
    base = posixpath.basename(path)
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


@dataclass
class Repo:
    owner: str
    name: str
    branch: str | None = None

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass
class GameFile:
    path: str  # relative to the game folder, with forward slashes
    data: bytes


@dataclass
class GameBundle:
    folder: str  # where in the repo the game was found ("" = the top)
    files: list[GameFile]
    skipped: list[str] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    manifest: bytes | None = None  # startng.json (game folder first, then the top of the repo)
    leaderboard: list[dict] | None = None  # its leaderboard stats (app/leaderboard.py); None = plain score
    description: str | None = None  # its one-line description, for the game's page on start.ng

    @property
    def total_bytes(self) -> int:
        return sum(len(f.data) for f in self.files)


def parse_repo_url(url: str, branch: str | None = None) -> Repo:
    m = REPO_RE.match(url.strip())
    if not m:
        raise PublishError("Use a public GitHub repo link, like https://github.com/you/your-game", "bad_link")
    branch = branch or m.group(3)
    if branch and (not BRANCH_RE.match(branch) or ".." in branch):
        raise PublishError(f'"{branch}" is not a valid branch name', "bad_branch")
    return Repo(owner=m.group(1), name=m.group(2), branch=branch)


def game_name(raw: str) -> str:
    """A safe subdomain name from what the builder asked for (or their repo name)."""
    name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", raw.lower())).strip("-")[:MAX_NAME].rstrip("-")
    try:
        return storage.validate_game_name(name)
    except storage.StorageError:
        if name in storage.reserved_names():
            raise PublishError(f'"{name}" is reserved, pick another name', "reserved_name")
        raise PublishError(f'"{raw}" can\'t be used as a game name: use letters, numbers and hyphens', "bad_name")


def download_repo(repo: Repo) -> bytes:
    """The repo as a zip, straight from GitHub. No git needed, and nothing in it is run."""
    ref = f"refs/heads/{repo.branch}" if repo.branch else "HEAD"
    url = f"https://github.com/{repo.owner}/{repo.name}/archive/{ref}.zip"
    try:
        with requests.get(url, stream=True, timeout=30) as res:
            if res.status_code == 404:
                raise PublishError("Repo or branch not found. Is the repo public?", "repo_not_found")
            res.raise_for_status()
            chunks, size = [], 0
            for chunk in res.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise PublishError(f"The repo is over {MAX_DOWNLOAD_BYTES // 1048576} MB to download", "repo_too_big")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as err:
        raise PublishError("Couldn't download the repo from GitHub, try again", "download_failed") from err


def _safe_path(name: str) -> str | None:
    """The entry's path inside the repo (without GitHub's top folder), or None to leave it out.

    Hidden files (.git, .env, ...), "..", backslashes and node_modules are never published.
    """
    top, _, path = name.partition("/")  # GitHub zips wrap everything in "<repo>-<ref>/"
    if not top or not path:
        return None
    try:
        path = storage.clean_path(path)  # the bucket's own path rules
    except storage.StorageError:
        return None
    return None if "node_modules" in path.split("/") else path


def _looks_built(html: str) -> bool:
    """A Vite/webpack source index.html loads /src/*.ts(x) and can't play without building."""
    return not re.search(r"""<script[^>]+src=["']/?src/[^"']+\.(tsx?|jsx)["']""", html, re.I)


MIN_GUESSED_DESCRIPTION = 20
README_NAMES = ("README.md", "readme.md", "Readme.md", "README.markdown", "README")
MAX_README_BYTES = 64 * 1024


def _meta_description(html_text: str) -> str | None:
    """The game's own <meta name="description"> (or og:description) from its index.html."""
    found: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", html_text, re.I):
        key = re.search(r"""(?:name|property)\s*=\s*["'](description|og:description)["']""", tag, re.I)
        content = re.search(r"""content\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.I)
        if key and content:
            found.setdefault(key.group(1).lower(), html.unescape(content.group(1) or content.group(2) or ""))
    for key in ("description", "og:description"):
        text = clean_description(found.get(key, ""))
        if text and len(text) >= MIN_GUESSED_DESCRIPTION:
            return text
    return None


def _readme_description(readme: str) -> str | None:
    """The first real paragraph of a README: not a heading, badge, picture or HTML."""
    paragraph: list[str] = []
    for line in readme.splitlines():
        line = line.strip()
        skip = not line or line.startswith(("#", "![", "[![", "<", "```", "|", "---", "===", ">"))
        if skip:
            if paragraph:
                break
            continue
        paragraph.append(line)
    text = " ".join(paragraph)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](link) -> text
    text = re.sub(r"[*_`]+", "", text)
    text = clean_description(text)
    return text if text and len(text) >= MIN_GUESSED_DESCRIPTION else None


def _guess_description(files: list["GameFile"], readme: bytes | None) -> str | None:
    """For a game with no description in startng.json: the one in its own page, then its README."""
    index = next((f for f in files if f.path == "index.html"), None)
    if index:
        text = _meta_description(index.data.decode("utf-8", "replace"))
        if text:
            return text
    if readme:
        return _readme_description(readme.decode("utf-8", "replace"))
    return None


def read_game(archive: bytes) -> GameBundle:
    """Find the ready-to-play game in a repo zip and check it."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as err:
        raise PublishError("GitHub sent a broken download, try again", "download_failed") from err
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise PublishError(f"The repo has more than {MAX_ARCHIVE_ENTRIES} files", "too_many_files")
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                continue  # links could point outside the game
            path = _safe_path(info.filename)
            if path:
                entries[path] = info

        folder = _find_game_folder(zf, entries)
        prefix = f"{folder}/" if folder else ""
        bundle = GameBundle(folder=folder, files=[])
        errors: list[Problem] = []
        total = count = 0
        for path, info in sorted(entries.items()):
            if not path.startswith(prefix):
                continue
            rel = path[len(prefix):]
            if _extension(rel) not in SERVED:
                bundle.skipped.append(rel)
                continue
            # Check sizes before reading anything (zip bombs claim small and unpack huge;
            # zipfile never returns more than the size an entry declares).
            count += 1
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES:
                errors.append(Problem("file_too_big", f"{rel} is over {MAX_FILE_BYTES // 1048576} MB", rel))
            elif total <= MAX_TOTAL_BYTES and count <= MAX_FILES:
                bundle.files.append(GameFile(rel, zf.read(info)))
        if total > MAX_TOTAL_BYTES:
            errors.append(Problem("game_too_big", f"The game is over {MAX_TOTAL_BYTES // 1048576} MB"))
        if count > MAX_FILES:
            errors.append(Problem("too_many_files", f"The game has more than {MAX_FILES} files"))

        readme = None
        for candidate in ([f"{prefix}{n}" for n in README_NAMES] if prefix else []) + list(README_NAMES):
            info = entries.get(candidate)
            if info and info.file_size <= MAX_README_BYTES:
                readme = zf.read(info)
                break

        for candidate in ([f"{prefix}{MANIFEST}"] if prefix else []) + [MANIFEST]:
            info = entries.get(candidate)
            if info and info.file_size <= MAX_MANIFEST_BYTES:
                bundle.manifest = zf.read(info)
                break

    if bundle.manifest is not None:
        try:
            bundle.leaderboard = parse_manifest(bundle.manifest)
            bundle.description = parse_description(bundle.manifest)
        except SchemaError as err:
            errors.append(Problem("bad_startng_json", f"startng.json: {err}", MANIFEST))
    bundle.description = bundle.description or _guess_description(bundle.files, readme)
    found, bundle.warnings = inspect([(f.path, f.data) for f in bundle.files])
    errors += found
    if errors:
        raise GameRejected(errors, bundle.warnings)
    return bundle


def _find_game_folder(zf: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo]) -> str:
    needs_build = False
    for folder in GAME_DIRS:
        index = entries.get(f"{folder}/index.html" if folder else "index.html")
        if not index:
            continue
        if index.file_size > MAX_FILE_BYTES:
            raise PublishError("index.html is too big", "file_too_big")
        if _looks_built(zf.read(index).decode("utf-8", "replace")):
            return folder
        needs_build = True
    if needs_build:
        raise PublishError(
            "This repo needs building first. Commit your built game (for example a dist/ folder), "
            "or publish a plain HTML/CSS/JS game.",
            "needs_build",
        )
    raise PublishError("No index.html found at the top of the repo or in dist/, build/, docs/ or out/", "no_index")
