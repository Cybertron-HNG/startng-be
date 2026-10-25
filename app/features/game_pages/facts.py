"""What a game's own files really say. Sources, best first: startng.json (the creator's own words), the README, then the
game's own HTML page. Nothing here guesses: a fact is either found in a file or it is missing."""
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

INDEX_PATHS = ["index.html", "dist/index.html", "build/index.html", "docs/index.html", "out/index.html"]
README_NAMES = ("README.md", "readme.md", "Readme.md", "README.markdown", "README")
MAX_BYTES = 400_000
MAX_LINE = 240
HOW = re.compile(r"how to play|controls?|instructions?|gameplay|how it works", re.I)
FEATURES = re.compile(r"features?|highlights?", re.I)
STORY = re.compile(r"story|premise|overview|synopsis|about", re.I)
WALK = re.compile(r"walk-?through|guide|tips|strategy|how to win|hints?", re.I)
CONTROL_WORDS = re.compile(r"\b(wasd|arrow keys?|arrows|space ?bar|spacebar|click|tap|swipe|mouse|drag|joystick|keyboard|press|hold)\b", re.I)
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


@dataclass
class Facts:
    title: str
    creator: str = "a builder"
    description: str | None = None
    story: str | None = None
    controls: list[str] = field(default_factory=list)  # things like "WASD or the arrow keys to move"
    steps: list[str] = field(default_factory=list)  # ordered how-to-play steps
    features: list[str] = field(default_factory=list)
    walkthrough: list[str] = field(default_factory=list)
    leaderboard: list[str] = field(default_factory=list)  # the labels the leaderboard ranks by
    board: list[dict] = field(default_factory=list)  # the leaderboard's stats as {label, order, format}: how it ranks
    published: str | None = None  # "March 2026": when the game first went live on start.ng
    tags: list[str] = field(default_factory=list)  # approved tags that say what kind of game it is
    sources: dict[str, str] = field(default_factory=dict)  # fact name -> the file it came from

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def clean(text: str, limit: int = MAX_LINE) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # a markdown link keeps its words
    text = re.sub(r"<[^>]+>", " ", text)  # tags first, before the ">" below goes
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -•\t")
    return text[:limit].rstrip()


def _lines(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [c for c in (clean(v) for v in value[:12] if isinstance(v, str)) if c]


def from_manifest(raw: bytes | None, facts: Facts) -> None:
    """startng.json: "description", "story", "controls", "howToPlay", "features", "walkthrough"."""
    if not raw or len(raw) > 64 * 1024:
        return
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    if isinstance(data.get("description"), str) and clean(data["description"], 400):
        facts.description = clean(data["description"], 400)
        facts.sources["description"] = "startng.json"
    if isinstance(data.get("story"), str) and clean(data["story"], 600):
        facts.story = clean(data["story"], 600)
        facts.sources["story"] = "startng.json"
    for key, attr in (("controls", "controls"), ("howToPlay", "steps"), ("features", "features"), ("walkthrough", "walkthrough")):
        lines = _lines(data.get(key))
        if lines:
            setattr(facts, attr, lines)
            facts.sources[attr] = "startng.json"


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """A README as (heading, lines) pieces. Lines before the first heading belong to ""."""
    out: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            out.append((clean(m.group(1)), []))
        elif line.strip():
            out[-1][1].append(line.rstrip())
    return out


def from_readme(raw: bytes | None, facts: Facts) -> None:
    if not raw:
        return
    text = raw[:MAX_BYTES].decode("utf-8", errors="ignore")
    bullets = lambda ls: [c for c in (clean(re.sub(r"^\s*[-*+]\s+", "", l)) for l in ls if re.match(r"^\s*[-*+]\s+", l)) if c]
    numbered = lambda ls: [c for c in (clean(re.sub(r"^\s*\d+[.)]\s+", "", l)) for l in ls if re.match(r"^\s*\d+[.)]\s+", l)) if c]
    prose = lambda ls: [clean(l, 600) for l in ls if not re.match(r"^\s*([-*+]|\d+[.)])\s+", l) and not l.lstrip().startswith(("|", "!", "<", "[!["))]
    for heading, lines in _sections(text):
        if not heading:
            continue
        if HOW.search(heading):
            if not facts.steps and numbered(lines):
                facts.steps, facts.sources["steps"] = numbered(lines)[:12], "README"
            if not facts.controls and (bullets(lines) or prose(lines)):
                facts.controls, facts.sources["controls"] = (bullets(lines) or [p for p in prose(lines) if p])[:8], "README"
        elif FEATURES.search(heading) and not facts.features and bullets(lines):
            facts.features, facts.sources["features"] = bullets(lines)[:10], "README"
        elif WALK.search(heading) and not facts.walkthrough and (numbered(lines) or bullets(lines)):
            facts.walkthrough, facts.sources["walkthrough"] = (numbered(lines) or bullets(lines))[:12], "README"
        elif STORY.search(heading) and not facts.story and [p for p in prose(lines) if p]:
            facts.story, facts.sources["story"] = [p for p in prose(lines) if p][0], "README"
    if not facts.story and not facts.description:
        # the first real paragraph, before or just under the title: not from the how to play, features or tips sections
        first = next((p for h, ls in _sections(text) if not (HOW.search(h) or FEATURES.search(h) or WALK.search(h)) for p in prose(ls) if len(p) >= 40), None)
        if first:
            facts.description, facts.sources["description"] = first[:400], "README"


class _Page(HTMLParser):
    """The words a game's HTML shows, and its title and meta description. Scripts and styles are skipped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta = ""
        self.blocks: list[tuple[str, str]] = []  # (tag, text)
        self._skip = 0
        self._tags: list[str] = []
        self._title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._title = True
        if tag == "meta" and (a.get("name") or "").lower() == "description":
            self.meta = a.get("content") or ""
        if tag in SKIP_TAGS and tag != "head":
            self._skip += 1
        self._tags.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if tag in SKIP_TAGS and tag != "head" and self._skip:
            self._skip -= 1
        if tag in self._tags:
            while self._tags and self._tags.pop() != tag:
                pass

    def handle_data(self, data):
        if self._title:
            self.title += data
            return
        text = clean(data, 400)
        if text and not self._skip:
            self.blocks.append((self._tags[-1] if self._tags else "", text))


def from_page(raw: bytes | None, facts: Facts) -> None:
    if not raw:
        return
    p = _Page()
    try:
        p.feed(raw[:MAX_BYTES].decode("utf-8", errors="ignore"))
    except Exception:  # a broken page: use nothing from it
        return
    meta = clean(p.meta, 400)
    if meta and not facts.description and len(meta) >= 20:
        facts.description, facts.sources["description"] = meta, "the game's page"
    if facts.controls or facts.steps:
        return
    # words under a heading like "How to play", then any short line that names a key or a gesture
    section: list[str] = []
    in_how = False
    for tag, text in p.blocks:
        if re.fullmatch(r"h[1-6]", tag):
            in_how = bool(HOW.search(text))
            continue
        if in_how and 6 <= len(text) <= 160:
            section.append(text)
    lines = section or [t for tag, t in p.blocks if tag not in ("button", "a", "title") and 8 <= len(t) <= 160 and CONTROL_WORDS.search(t)]
    seen: list[str] = []
    for line in lines:
        if line not in seen:
            seen.append(line)
    if seen:
        facts.controls, facts.sources["controls"] = seen[:8], "the game's page"


def read_facts(store, game, tags: list[str]) -> Facts:
    """Facts for a Game row, read from its files through a GameStore (R2 on the server, a folder on a laptop)."""
    from ...routers.pages import _player_name, _title

    f = Facts(title=_title(game.name), creator=_player_name(game.owner, "a builder") if game.owner is not None else "a builder", tags=tags)
    if game.description:
        f.description, f.sources["description"] = clean(game.description, 400), "the game's description"
    paths = set(store.paths(game.name)) if store is not None else set()
    if store is not None:
        from_manifest(store.read_file(game.name, "startng.json") if "startng.json" in paths else None, f)
        readme = next((n for n in README_NAMES if n in paths), None)
        from_readme(store.read_file(game.name, readme) if readme else None, f)
        index = next((p for p in INDEX_PATHS if p in paths), None)
        from_page(store.read_file(game.name, index) if index else None, f)
    if game.leaderboard:
        f.leaderboard = [clean(c.get("label") or c.get("key") or "", 40) for c in game.leaderboard if isinstance(c, dict)]
        f.leaderboard = [c for c in f.leaderboard if c]
        f.board = [
            {"label": clean(c.get("label") or c.get("key") or "", 40), "order": "lowest" if c.get("order") == "lowest" else "highest", "format": "time" if c.get("format") == "time" else "number"}
            for c in game.leaderboard if isinstance(c, dict) and (c.get("label") or c.get("key"))
        ]
    if getattr(game, "created_at", None):
        f.published = game.created_at.strftime("%B %Y")
    return f
