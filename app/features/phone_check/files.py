"""The part of the phone check that reads a game's files (no browser): its viewport tag and fixed widths."""
import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

PHONE = 375  # CSS pixels: a page or canvas wider than this doesn't fit a common phone
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_WIDTH = re.compile(r"(?<![-\w])(min-width|width)\s*:\s*(\d+(?:\.\d+)?)px", re.I)
_SCALES = re.compile(r"(?<![-\w])(max-width|width)\s*:\s*[\d.]+(%|vw|vmin|dvw|svw)|max-width\s*:\s*100%|object-fit", re.I)
# Selectors for the page itself and its game area. A fixed width on a dialog or a button isn't the page's width.
_PAGE = re.compile(r"(^|[\s,>])(html|body|canvas|main|#game\w*|#app|#root|\.game\w*)(?![-\w])", re.I)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.viewport: str | None = None
        self.stylesheets: list[str] = []
        self.css: list[str] = []
        self.canvases: list[dict] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "meta" and a.get("name", "").lower() == "viewport":
            self.viewport = a.get("content", "")
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower() and a.get("href"):
            self.stylesheets.append(a["href"])
        elif tag == "style":
            self._in_style = True
        elif tag == "canvas":
            self.canvases.append(a)
        if a.get("style") and tag in ("html", "body", "canvas", "main"):
            self.css.append(f"{tag}{{{a['style']}}}")

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.css.append(data)


def _local_path(src: str) -> str | None:
    parts = urlsplit(src.strip())
    if parts.scheme or parts.netloc or not parts.path:
        return None
    return unquote(urljoin("/index.html", parts.path)).lstrip("/")


def _rules(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)  # rules inside media queries already adapt
    return _RULE.findall(css)


def check(read_file) -> list[dict] | None:
    """Findings from a game's files, [] when none, or None when there is no index.html. read_file(path) -> bytes|None."""
    page = read_file("index.html")
    if page is None:
        return None
    parser = _Page()
    try:
        parser.feed(page.decode("utf-8", "replace"))
    except Exception:
        pass
    found: list[dict] = []
    if parser.viewport is None:
        found.append({"code": "no_viewport", "message": 'index.html has no <meta name="viewport"> tag, so phones show '
                      'the page zoomed out, as on a computer. Add <meta name="viewport" content="width=device-width, '
                      'initial-scale=1">.'})
    elif "width=device-width" not in parser.viewport.replace(" ", "").lower():
        found.append({"code": "viewport_width", "message": f'The viewport tag says "{parser.viewport[:80]}": without '
                      "width=device-width, phones don't use their own width. Use width=device-width."})
    css = list(parser.css)
    for href in parser.stylesheets[:10]:
        path = _local_path(href)
        if path:
            data = read_file(path)
            if data:
                css.append(data[:200_000].decode("utf-8", "replace"))
    rules = [r for text in css for r in _rules(text)]
    for selector, body in rules:
        if not _PAGE.search(selector):
            continue
        wide = [int(float(px)) for prop, px in _WIDTH.findall(body) if float(px) > PHONE]
        if wide:
            found.append({"code": "fixed_width", "message": f'"{" ".join(selector.split())[:60]}" is {max(wide)} px wide, '
                          f"wider than a phone ({PHONE} px), so the page scrolls sideways or is cut off. Use max-width, "
                          "% or vw instead."})
            break
    canvas_scales = any(_SCALES.search(body) for selector, body in rules if re.search(r"canvas", selector, re.I))
    for canvas in parser.canvases:
        width = canvas.get("width", "")
        if width.isdigit() and int(width) > PHONE and not canvas_scales and not _SCALES.search(canvas.get("style", "")):
            found.append({"code": "wide_canvas", "message": f"A canvas is {width} px wide and nothing scales it down, so "
                          f"on a phone it's cut off or scrolls sideways. Give it max-width: 100% (or size it from the "
                          "window in your code)."})
            break
    return found
