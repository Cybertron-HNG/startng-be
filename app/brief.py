"""A short brief about a game, read from its repo's own files when it is published (or later, for games already live).

A game's built file is mostly the libraries it was built with, so it says almost nothing about the game. Its source does:
"RUN! Escape the boulder", "The vault is open. Take the golden idol". This reads the README and the text inside the
source (messages, opening comments, file names) and keeps the few lines that are the game's own words. Files are only
read as text: nothing in a repo is ever run. The result is small (a few KB), kept on the game, and used to write its pages.

    {"v": 1, "readme": "...", "messages": ["..."], "notes": ["..."], "names": ["level", "boulder"], "files": 12}
"""
import re
import zipfile
from datetime import datetime, timezone

README_NAMES = ("README.md", "readme.md", "Readme.md", "README.markdown", "README")
MAX_README = 8_000
MAX_MESSAGES = 40
MAX_NOTES = 12
MAX_NAMES = 40
MAX_FILE_BYTES = 250_000  # a bigger file is a bundle, not something a person wrote
MAX_LINE = 1_500  # a line this long is minified code
MAX_READ_BYTES = 2_500_000  # all the source read, so one huge repo can't slow publishing down
MAX_FILES = 400

SOURCE_EXT = {"js", "mjs", "jsx", "ts", "tsx", "html", "htm", "json"}
SKIP_DIRS = {"node_modules", "vendor", "vendors", "lib", "libs", "third_party", "third-party", "three", "dist", "build", "out", "docs",
             "coverage", ".github", "test", "tests", "__tests__", "spec", "e2e", "scripts", "tools"}
SKIP_FILES = {"package.json", "package-lock.json", "tsconfig.json", "startng.json", "manifest.json", "composer.json", "yarn.lock"}
GENERIC_NAMES = {"src", "assets", "asset", "js", "css", "img", "image", "images", "index", "main", "app", "game", "games", "utils", "util", "lib",
                 "shared", "common", "components", "component", "public", "static", "test", "config", "readme", "package", "style", "styles",
                 "vendor", "three", "min", "bundle", "data", "core", "helpers", "helper", "types", "constants", "scene", "scenes", "html", "json",
                 "png", "jpg", "jpeg", "webp", "svg", "mp3", "ogg", "wav", "glb", "gltf", "txt", "md", "ts", "tsx", "jsx", "mjs", "ui", "hud"}

# A string literal in source code ('...', "..." or a template string with nothing interpolated)
LITERAL = re.compile(r"""(["'`])((?:\\.|(?!\1)[^\\\n]){25,200})\1""")
HTML_TEXT = re.compile(r">([^<>]{25,200})<")
SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
# Messages that belong to a library or to the platform, not to the game
NOISE = re.compile(
    r"THREE\.|Object3D|BufferGeometry|WebGL|shader|texture|Cannot |can not |can't be|is not |isn't |must be|Invalid |undefined|not supported|"
    r"Unable to|Failed to|Could not|expected |deprecated|Uncaught|TypeError|RangeError|Error:|Warning:|console\.|stack trace|polyfill|"
    r"https?:|start\.ng|[Ss]ign in|[Ll]eaderboard|copyright|licen[cs]e|permission is hereby|\.js\b|\.css\b|\.png\b|\.json\b",
    re.I,
)
CODE_CHARS = re.compile(r"[{};=<>()\[\]\\|@#$%^*_~/]")
LICENSE = re.compile(r"copyright|licen[cs]e|permission is hereby|@license|eslint|prettier|todo|fixme|hack|generated|do not edit", re.I)


def _looks_like_prose(s: str) -> bool:
    s = " ".join(s.split())
    if not 25 <= len(s) <= 200 or len(s.split()) < 4:
        return False
    if not (s[0].isupper() or s[0].isdigit() or s[0] in "\"'“"):
        return False  # a fragment (it starts mid-sentence), or a code-ish string
    if CODE_CHARS.search(s) or NOISE.search(s):
        return False
    letters = sum(c.isalpha() or c == " " for c in s)
    return letters / len(s) >= 0.8


def _words(path: str) -> list[str]:
    out = []
    for part in re.split(r"[/\\]", path):
        stem = part.rsplit(".", 1)[0] if "." in part else part
        for w in re.sub(r"([a-z])([A-Z])", r"\1 \2", stem).replace("-", " ").replace("_", " ").split():
            w = w.lower()
            if w.isalpha() and 3 <= len(w) <= 24 and w not in GENERIC_NAMES:
                out.append(w)
    return out


def _skip(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if any(p.lower() in SKIP_DIRS for p in parts[:-1]) or name in SKIP_FILES or name.endswith((".min.js", ".bundle.js", ".map")):
        return True
    return name.rsplit(".", 1)[-1].lower() not in SOURCE_EXT if "." in name else True


def _text_of(path: str, raw: bytes) -> str | None:
    text = raw.decode("utf-8", errors="ignore")
    if any(len(line) > MAX_LINE for line in text.splitlines()):
        return None  # minified or bundled: not written by a person
    return text


def extract(entries: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile, readme: bytes | None = None) -> dict:
    """The brief for a repo already opened as a zip. `entries` maps each repo path to its zip entry; `readme` is its README if known."""
    messages: list[str] = []
    notes: list[str] = []
    names: list[str] = []
    seen: set[str] = set()
    read = files = 0
    for path, info in sorted(entries.items()):
        for w in _words(path):
            if w not in names and len(names) < MAX_NAMES:
                names.append(w)
        if _skip(path) or info.file_size > MAX_FILE_BYTES or files >= MAX_FILES or read + info.file_size > MAX_READ_BYTES:
            continue
        text = _text_of(path, zf.read(info))
        if text is None:
            continue
        read += info.file_size
        files += 1
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in ("html", "htm"):
            found = [m.group(1) for m in HTML_TEXT.finditer(SCRIPT_OR_STYLE.sub(" ", text))]
        else:
            found = [m.group(2) for m in LITERAL.finditer(text) if "${" not in m.group(2)]
            # the comments at the top of a source file often say what it is
            for line in text.splitlines()[:12]:
                c = line.strip().lstrip("/*# ").strip()
                if line.strip().startswith(("//", "/*", "*")) and 25 <= len(c) <= 200 and len(c.split()) >= 4 and c[0].isupper() and not LICENSE.search(c):
                    if c not in notes and len(notes) < MAX_NOTES:
                        notes.append(c)
        for s in found:
            s = " ".join(s.split())
            key = s.lower()
            if key not in seen and _looks_like_prose(s) and len(messages) < MAX_MESSAGES:
                seen.add(key)
                messages.append(s)
    text = readme.decode("utf-8", errors="ignore")[:MAX_README] if readme else None
    return {
        "v": 1,
        "readme": text.strip() if text and text.strip() else None,
        "messages": messages,
        "notes": notes,
        "names": names,
        "files": files,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def find_readme(entries: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile, prefix: str = "") -> bytes | None:
    """The README next to the game, else the one at the top of the repo."""
    for candidate in ([f"{prefix}{n}" for n in README_NAMES] if prefix else []) + list(README_NAMES):
        info = entries.get(candidate)
        if info and info.file_size <= 256 * 1024:
            return zf.read(info)
    return None
