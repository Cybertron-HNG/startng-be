"""Draft pages from facts. A page is only written when the facts are enough; otherwise build() says why not (a string).
Everything on a page is either a fact from the game's files or a sentence that is true for every start.ng game
(it is free, it runs in the browser, it has nothing to install)."""
import re
from dataclasses import dataclass

from .facts import Facts

KINDS = ["about", "how-to-play", "faq", "walkthrough"]
LABELS = {"about": "About {t}", "how-to-play": "How to play {t}", "faq": "{t} FAQ", "walkthrough": "{t} walkthrough"}
SHORT = {"about": "About", "how-to-play": "How to play", "faq": "FAQ", "walkthrough": "Walkthrough"}


@dataclass
class Draft:
    title: str
    description: str
    body: str


def _sentence(s: str) -> str:
    s = s.strip()
    return s if s[-1:] in ".!?" else s + "."


def _first_sentence(s: str, limit: int = 150) -> str:
    m = re.match(r"(.{20,}?[.!?])(\s|$)", s)
    return (m.group(1) if m else s)[:limit].rstrip()


def _meta(*parts: str) -> str:
    text = " ".join(_sentence(p) for p in parts if p)
    return text if len(text) <= 158 else text[:155].rstrip() + "…"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def build(kind: str, f: Facts, name: str) -> Draft | str:
    t = f.title
    title = LABELS[kind].format(t=t)
    text = f.description or f.story
    if kind == "about":
        if not text and len(f.features) < 2:
            return "No description or README text to write from. The creator can add a description in startng.json."
        parts = ["## What is %s?" % t]
        if f.description:
            parts.append(_sentence(f.description))
        if f.story and f.story != f.description:
            parts.append(_sentence(f.story))
        if len(f.features) >= 1:
            parts += ["## Features"] + [f"- {x}" for x in f.features]
        if f.leaderboard:
            parts += ["## Leaderboard", f"{t} has a leaderboard. Scores are ranked by {_and(f.leaderboard)}."]
        parts += [f"## Play {t}", f"{t} is free to play in your browser on start.ng, with nothing to download. It was made by {f.creator}."]
        if f.tags:
            parts.append(f"It is a game for people who like {_and([x.lower() for x in f.tags[:4]])}.")
        return Draft(title, _meta(_first_sentence(text) if text else f"{t} is a free browser game on start.ng", "Play free in your browser on start.ng"), "\n\n".join(parts))
    if kind == "how-to-play":
        if not f.controls and not f.steps:
            return "No controls or steps found in the game's files. The creator can add a \"controls\" or \"howToPlay\" list in startng.json."
        parts = []
        if f.controls:
            parts += ["## Controls"] + [f"- {x}" for x in f.controls]
        if f.steps:
            parts += ["## How to play"] + [f"{i}. {x}" for i, x in enumerate(f.steps, 1)]
        parts += [f"## Start playing", f"{t} is free in your browser at {name}.start.ng. Nothing to download."]
        lead = f.steps[0] if f.steps else f.controls[0]
        return Draft(title, _meta(f"How to play {t}", lead, "Free in your browser on start.ng"), "\n\n".join(parts))
    if kind == "faq":
        qa: list[tuple[str, str]] = []
        unique = 0
        said = bool(text or f.controls or f.steps)  # something the game itself said: tags and a leaderboard alone are not enough
        if text:
            qa.append((f"What is {t}?", _sentence(text if len(text) <= 300 else _first_sentence(text, 300)))); unique += 1
        qa.append((f"Is {t} free to play?", f"Yes. You can play {t} for free in your browser on start.ng."))
        qa.append((f"Do I need to download or install {t}?", f"No. {t} runs in your browser at {name}.start.ng. There is nothing to install."))
        qa.append((f"Who made {t}?", f"{t} was made by {f.creator}."))
        if f.leaderboard:
            qa.append((f"Does {t} have a leaderboard?", f"Yes. Scores are ranked by {_and(f.leaderboard)}.")); unique += 1
        if f.controls or f.steps:
            how = f.steps or f.controls
            qa.append((f"How do I play {t}?", " ".join(_sentence(x) for x in how[:4]))); unique += 1
        if f.tags:
            qa.append((f"What kind of game is {t}?", f"{t} is in these categories: {_and(f.tags[:5])}.")); unique += 1
        if not said or unique < 2:
            return "Not enough to answer questions about this game. It needs a description or a way to play in its files."
        body = "\n\n".join(f"## {q}\n\n{a}" for q, a in qa)
        return Draft(title, _meta(f"Questions and answers about {t}", "Is it free? Who made it? How do you play?"), body)
    if kind == "walkthrough":
        if len(f.walkthrough) < 3:
            return "No walkthrough in the game's files (it needs at least 3 steps or tips). The creator can add a \"walkthrough\" list in startng.json."
        items = [f"{i}. {x}" for i, x in enumerate(f.walkthrough, 1)]
        body = f"## {t} walkthrough\n\n" + "\n".join(items) + f"\n\n## Play {t}\n\nPlay {t} free in your browser at {name}.start.ng."
        return Draft(title, _meta(f"A walkthrough of {t}", f.walkthrough[0]), body)
    raise ValueError(kind)


# ---- a small markdown: ## headings, paragraphs, - bullets, 1. steps. Everything is escaped.

def _inline(s: str) -> str:
    import html
    return html.escape(s, quote=True)


def to_html(body: str) -> str:
    out: list[str] = []
    mode = ""  # "", "ul", "ol"

    def close():
        nonlocal mode
        if mode:
            out.append(f"</{mode}>")
            mode = ""

    for line in body.splitlines():
        s = line.strip()
        if not s:
            close()
            continue
        if s.startswith("## "):
            close()
            out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif re.match(r"^[-*] ", s):
            if mode != "ul":
                close(); out.append("<ul>"); mode = "ul"
            out.append(f"<li>{_inline(s[2:])}</li>")
        elif re.match(r"^\d+[.)] ", s):
            if mode != "ol":
                close(); out.append("<ol>"); mode = "ol"
            out.append(f"<li>{_inline(re.sub(r'^\d+[.)] ', '', s))}</li>")
        else:
            close()
            out.append(f"<p>{_inline(s)}</p>")
    close()
    return "\n".join(out)


def faq_pairs(body: str) -> list[tuple[str, str]]:
    pairs, q, a = [], None, []
    for line in body.splitlines():
        if line.startswith("## "):
            if q is not None and a:
                pairs.append((q, " ".join(a)))
            q, a = line[3:].strip(), []
        elif line.strip() and q is not None:
            a.append(line.strip())
    if q is not None and a:
        pairs.append((q, " ".join(a)))
    return pairs


def ordered_steps(body: str) -> list[str]:
    return [re.sub(r"^\d+[.)] ", "", l.strip()) for l in body.splitlines() if re.match(r"^\s*\d+[.)] ", l)]


def shingles(body: str, title: str, n: int = 4) -> set[str]:
    """Words in groups of n, with the game's name taken out, to tell whether two pages say nearly the same thing."""
    body = re.sub(r"[a-z0-9-]+\.start\.ng", "x.start.ng", body, flags=re.I)  # a game's own address is not what the page says
    words = re.sub(re.escape(title), "X", body, flags=re.I).lower().split()
    return {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0
