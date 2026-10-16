"""What start.ng looks for inside a game before it goes live.

Every problem has a code (for scripts and AI tools) and a message (for people). Errors stop
the game going live; warnings are reported but don't. publish.read_game runs these after the
repo's structure checks (index.html, sizes...), and POST /games/check runs everything without
publishing.

Patterns are written so they never spell out a signature as plain text: the team's CI and
Docker malware gate scans this repo too.
"""
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_PROBLEMS = 50  # per list, so one broken repo can't produce a huge reply

PATTERNS_FILE = Path(__file__).resolve().parent.parent / "malware-patterns.txt"


@dataclass
class Problem:
    code: str
    message: str
    file: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


# ---- what's read

TEXT = {"html", "htm", "css", "js", "mjs", "json", "map", "txt", "svg"}
FONTS = {"woff", "woff2", "ttf", "otf"}  # a known place to hide a payload
HTML = {"html", "htm"}
SCRIPT = {"js", "mjs", "html", "htm"}

# ---- errors: the game can't go live

# Browser crypto miners and the pools they talk to.
MINER = re.compile(
    rb"coin-?hive|crypto-?night|crypto-?loot|coin-?imp|webmine-?pool|deep-?miner|minero\.cc|stratum\+(?:tcp|ssl)://"
    rb"|authed-?mine|jse-?coin|monero-?miner|cryptominer",
    re.I,
)
# Running code that was hidden in a string: eval/Function over a decoded string.
DECODE_AND_RUN = re.compile(
    rb"\b(?:eval|Function)\s*\(\s*(?:window\.)?(?:atob|unescape|decodeURIComponent|String\s*\.\s*fromCharCode)\s*\("
)
# javascript-obfuscator output: thousands of _0x1a2b names. Normal minified code has none.
OBFUSCATED_NAME = re.compile(rb"\b_0x[0-9a-f]{4,6}\b")
OBFUSCATED_NAMES = 300
# A long run of \x41-style escapes: a script hidden inside a string.
ESCAPED_RUN = re.compile(rb"(?:\\x[0-9a-fA-F]{2}){300,}")
# Password boxes: start.ng does sign-in, so a game asking for a password is a phishing risk.
PASSWORD_HTML = re.compile(rb"""<input\b[^>]*\btype\s*=\s*["']?password\b""", re.I)
PASSWORD_JS = re.compile(rb"""\btype\s*[=:]\s*["']password["']""", re.I)
# A page that sends players elsewhere as soon as it opens.
META_REFRESH = re.compile(rb"""<meta\b[^>]*http-equiv\s*=\s*["']?refresh[^>]*>""", re.I)
URL_IN = re.compile(rb"""url\s*=\s*['"]?([^'">\s;]+)""", re.I)
# Programs dressed up as game files, or carried inside them as base64.
PROGRAM_START = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")
PROGRAM_BASE64 = re.compile(rb"TVqQAAMAAAAEAAAA|f0VMRgI[BC]AQ")

# ---- warnings

TRUSTED_SCRIPT_HOSTS = {
    "start.ng", "cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "esm.sh", "cdn.skypack.dev",
    "ga.jspm.io", "ajax.googleapis.com", "code.jquery.com", "cdn.tailwindcss.com",
}
SCRIPT_SRC = re.compile(rb"""<script\b[^>]*\bsrc\s*=\s*["']?((?:https?:)?//[^"'\s>]+)""", re.I)
# Real imports only: statements at the start of a line, and import("...") calls; not URLs that
# libraries mention in their comments.
IMPORT_URL = re.compile(
    rb"""^[ \t]*(?:import|export)\b[^;'"\n]*?["']((?:https?:)?//[^"']+)["']|\bimport\(\s*["']((?:https?:)?//[^"']+)["']""",
    re.M,
)
PLAIN_HTTP = re.compile(rb"""\b(?:src|href)\s*=\s*["']?(http://[^"'\s>]+)""", re.I)
LEAVES = re.compile(
    rb"""\blocation(?:\.href)?\s*=\s*["'`](https?://[^"'`]+)|\blocation\.(?:replace|assign)\(\s*["'`](https?://[^"'`]+)"""
)
USES_STARTNG = re.compile(rb"startng:whoami|startng_session|api\.start\.ng|start\.ng/connect")
COVERS = {"cover.png", "cover.jpg", "cover.jpeg", "cover.webp"}


def _extension(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _host(url: bytes) -> str:
    try:
        return (urlsplit(url.decode("utf-8", "replace")).hostname or "").lower()
    except ValueError:
        return ""


def _is_startng(host: str) -> bool:
    return host == "start.ng" or host.endswith(".start.ng")


def _trusted(host: str) -> bool:
    return _is_startng(host) or host in TRUSTED_SCRIPT_HOSTS


def _malware_patterns() -> list[re.Pattern[bytes]]:
    lines = PATTERNS_FILE.read_text(encoding="utf-8").splitlines()
    return [re.compile(line.strip().encode()) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def inspect(files: list[tuple[str, bytes]]) -> tuple[list[Problem], list[Problem]]:
    """(errors, warnings) for a game's files: (path, contents) pairs, paths from the game folder."""
    errors: list[Problem] = []
    warnings: list[Problem] = []
    malware = _malware_patterns()
    uses_startng = False

    for path, data in files:
        ext = _extension(path)

        if data.startswith(PROGRAM_START):
            errors.append(Problem("hidden_program", f"{path} is really a program, not a game file", path))
            continue
        if ext not in TEXT | FONTS and ext != "wasm":
            continue  # pictures, sounds, video

        if any(p.search(data) for p in malware):
            errors.append(Problem("malware", f"{path} matches a known malware pattern", path))
            continue
        if MINER.search(data):
            errors.append(Problem("crypto_miner", f"{path} looks like a crypto miner", path))
            continue
        if ext not in TEXT:
            continue  # fonts and wasm: signatures only

        if PROGRAM_BASE64.search(data):
            errors.append(Problem("hidden_program", f"{path} carries a program hidden as text", path))
        if ext in SCRIPT:
            if DECODE_AND_RUN.search(data):
                errors.append(Problem("obfuscated_code", f"{path} runs code hidden in an encoded string", path))
            elif len(OBFUSCATED_NAME.findall(data)) > OBFUSCATED_NAMES or ESCAPED_RUN.search(data):
                errors.append(Problem("obfuscated_code", f"{path} is obfuscated, so it can't be checked. Publish the readable code", path))
            if PASSWORD_JS.search(data) or (ext in HTML and PASSWORD_HTML.search(data)):
                errors.append(Problem(
                    "password_field",
                    f"{path} asks for a password. Games never do: start.ng handles sign-in (see the docs, Sign in)",
                    path,
                ))
            uses_startng = uses_startng or bool(USES_STARTNG.search(data))

        if ext in HTML:
            for tag in META_REFRESH.findall(data):
                url = URL_IN.search(tag)
                if url and _host(url.group(1)) and not _is_startng(_host(url.group(1))):
                    errors.append(Problem("redirect", f"{path} sends players to {_host(url.group(1))} as soon as it opens", path))
            for url in SCRIPT_SRC.findall(data):
                host = _host(url if not url.startswith(b"//") else b"https:" + url)
                if host and not _trusted(host):
                    warnings.append(Problem(
                        "external_script",
                        f"{path} loads a script from {host}: whatever is there runs in your game. "
                        "Copy it into your repo or use a known CDN",
                        path,
                    ))
            for url in PLAIN_HTTP.findall(data):
                warnings.append(Problem(
                    "insecure_link",
                    f"{path} loads {url.decode('utf-8', 'replace')[:80]} over http://, which browsers block on https sites. Use https://",
                    path,
                ))
        if ext in {"js", "mjs"}:
            for statement, call in IMPORT_URL.findall(data):
                url = statement or call
                host = _host(url if not url.startswith(b"//") else b"https:" + url)
                if host and not _trusted(host):
                    warnings.append(Problem(
                        "external_script",
                        f"{path} imports code from {host}: whatever is there runs in your game. "
                        "Copy it into your repo or use a known CDN",
                        path,
                    ))
        if ext in SCRIPT:
            for a, b in LEAVES.findall(data):
                host = _host(a or b)
                if host and not _is_startng(host):
                    warnings.append(Problem("leaves_start_ng", f"{path} sends players to {host}", path))

    names = {p.rsplit("/", 1)[-1].lower() for p, _ in files if "/" not in p}
    if not names & COVERS:
        warnings.append(Problem("no_cover", "No cover.png next to index.html, so your card shows a letter. Add a 16:10 picture"))
    if not uses_startng:
        warnings.append(Problem(
            "no_start_ng_player",
            "The game doesn't ask start.ng who's playing. Every start.ng game shows the player's character (see the docs)",
        ))
    return _dedupe(errors)[:MAX_PROBLEMS], _dedupe(warnings)[:MAX_PROBLEMS]


def _dedupe(problems: list[Problem]) -> list[Problem]:
    seen, out = set(), []
    for p in problems:
        key = (p.code, p.message)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
