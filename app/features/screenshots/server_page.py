"""Screenshots on a game's server-written page (start.ng/games/<name>), so search engines see what people see: the same
words and classes as src/features/screenshots/GameScreenshots.tsx, and the pictures in the page's VideoGame JSON-LD."""
from html import escape

from sqlalchemy.orm import Session

from ...routers.pages import GAME_LD_PARTS, GAME_PAGE_PARTS
from . import service


def game_page_part(db: Session, game) -> str:
    shots = service.screenshots_of(db, game.name)
    if not shots:
        return ""
    items = "".join(
        f'<li><a href="{escape(s["url"])}"><img src="{escape(s["url"])}" alt="{escape(s["alt"])}" loading="lazy" /></a></li>' for s in shots
    )
    return f'<section class="game-shots" aria-label="Screenshots"><p class="eyebrow">Screenshots</p><ul>{items}</ul></section>'


def add_to_ld(db: Session, game, ld: dict) -> None:
    shots = service.screenshots_of(db, game.name)
    if shots:
        ld["screenshot"] = [s["url"] for s in shots]


GAME_PAGE_PARTS.append(game_page_part)
GAME_LD_PARTS.append(add_to_ld)
