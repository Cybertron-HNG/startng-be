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
# Tags inside a sentence: their words belong to the paragraph around them ("<kbd>Space</kbd> to jump" is one line, not two)
INLINE_TAGS = {"b", "strong", "em", "i", "u", "s", "kbd", "code", "samp", "var", "span", "small", "mark", "abbr", "sub", "sup"}


@dataclass
class Facts:
    title: str
    creator: str = "a builder"
    description: str | None = None
    story: str | None = None
    messages: list[str] = field(default_factory=list)  # lines the game itself shows or says (from its source, app/brief.py)
    hints: list[str] = field(default_factory=list)  # the ones that tell the player what to do
    notes: list[str] = field(default_factory=list)  # opening comments in its source: kept for the reviewer, not shown
    about_more: list[str] = field(default_factory=list)  # more of what the game says about itself: README paragraphs, then its own page's text
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
    # whatever else the README says about the game (not the description or story already used, nor how to play, features or tips)
    used = {facts.description, facts.story}
    for heading, lines in _sections(text):
        if HOW.search(heading) or FEATURES.search(heading) or WALK.search(heading):
            continue
        for p in prose(lines):
            if len(p) >= 60 and p not in used and p not in facts.about_more and len(facts.about_more) < 3:
                facts.about_more.append(p)


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
        self._words: list[str] = []  # the block being read, inline tags and all
        self._block = ""

    def _flush(self):
        text = clean("".join(self._words), 400)
        if text:
            self.blocks.append((self._block, text))
        self._words = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in INLINE_TAGS:
            self._flush()
        if tag == "title":
            self._title = True
        if tag == "meta" and (a.get("name") or "").lower() == "description":
            self.meta = a.get("content") or ""
        if tag in SKIP_TAGS and tag != "head":
            self._skip += 1
        self._tags.append(tag)

    def handle_endtag(self, tag):
        if tag not in INLINE_TAGS:
            self._flush()
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
        if not self._skip:
            self._block = next((t for t in reversed(self._tags) if t not in INLINE_TAGS), "")
            self._words.append(data)

    def close(self):
        super().close()
        self._flush()


def from_page(raw: bytes | None, facts: Facts) -> None:
    if not raw:
        return
    p = _Page()
    try:
        p.feed(raw[:MAX_BYTES].decode("utf-8", errors="ignore"))
        p.close()
    except Exception:  # a broken page: use nothing from it
        return
    meta = clean(p.meta, 400)
    if meta and not facts.description and len(meta) >= 20:
        facts.description, facts.sources["description"] = meta, "the game's page"
    used = {facts.description, facts.story, *facts.about_more}
    for tag, text in p.blocks:
        if (tag in ("p", "blockquote", "div", "span") and 60 <= len(text) <= 300 and len(text.split()) >= 9 and text not in used
                and not CONTROL_WORDS.search(text) and len(facts.about_more) < 4):
            facts.about_more.append(text)
            used.add(text)
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


# A line from a game that tells the player what to do ("RUN! Escape the boulder", "Climb the ledge", "Press R to reload")
INSTRUCTION = re.compile(
    r"^(press|click|tap|hold|use|climb|collect|avoid|find|escape|take|reach|run|jump|pull|push|open|defeat|survive|get|move|aim|shoot|dodge|"
    r"swipe|drag|grab|follow|keep|save|rescue|catch|stack|match|drive|steer|fly|swim|dash|slide|hide|wait|watch|don't|do not|try|stay)\b",
    re.I,
)
MAX_SHOWN = 8


def from_brief(brief: dict | None, facts: Facts, readme_found: bool) -> None:
    """What the game's repo says about it (app/brief.py): its README when the game's own files have none, and its own messages."""
    if not isinstance(brief, dict):
        return
    readme = brief.get("readme")
    if isinstance(readme, str) and readme.strip() and not readme_found:
        from_readme(readme.encode("utf-8"), facts)
        facts.sources.setdefault("readme", "the game's repo")
    hints, said = [], []
    raw_messages = brief.get("messages")
    for m in raw_messages if isinstance(raw_messages, list) else []:
        m = clean(m, 200) if isinstance(m, str) else ""
        if not m or m in hints or m in said:
            continue
        (hints if INSTRUCTION.match(m) or CONTROL_WORDS.search(m) else said).append(m)
    if hints or said:
        facts.hints, facts.messages = hints[:MAX_SHOWN], said[:MAX_SHOWN]
        facts.sources["messages"] = "the game's source"
    raw_notes = brief.get("notes")
    facts.notes = [clean(n, 200) for n in (raw_notes if isinstance(raw_notes, list) else [])[:6] if isinstance(n, str) and clean(n, 200)]


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
    from_brief(getattr(game, "brief", None), f, readme_found=any(n in paths for n in README_NAMES))
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
