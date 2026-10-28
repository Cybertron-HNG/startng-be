"""The "Play here" link on a game's server-written page: the same words, address and classes as the button in
startng-fe src/features/play_here/PlayHereButton.tsx, so search engines read what players see."""
from sqlalchemy.orm import Session

from ... import flags
from ...routers.pages import GAME_PAGE_PARTS
from ...server_html import esc

# Addresses of this form open the game in the page (?play=here), on the website as on this page.
PLAY_HERE = "/games/{name}?play=here"


def game_page_part(db: Session, game) -> str:
    if not flags.is_on("play_here"):
        return ""
    href = esc(PLAY_HERE.format(name=game.name))
    return f'<p class="play-here-link"><a class="btn ghost" href="{href}">Play here</a></p>'


GAME_PAGE_PARTS.append(game_page_part)
