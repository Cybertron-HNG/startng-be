"""The remix lines on a game's server-written page (/games/<name>): the same words, links and class names as
src/features/remix/GameRemix.tsx in startng-fe, so what search engines read is what people see."""
from sqlalchemy.orm import Session

from ... import flags
from ...routers.games import _player_name
from ...routers.pages import GAME_PAGE_PARTS, _title
from ...server_html import esc
from . import service


def times(n: int) -> str:
    return "once" if n == 1 else f"{n} times"


def game_page_part(db: Session, game) -> str:
    if not flags.is_on("remix"):
        return ""
    found = service.info(db, game)
    lines = []
    if found.remix_of is not None:
        o = found.remix_of
        lines.append(f'<p>Remixed from <a href="/games/{esc(o.name)}">{esc(_title(o.name))}</a> by {esc(_player_name(o.owner, "a builder"))}</p>')
    if found.count:
        links = ", ".join(f'<a href="/games/{esc(g.name)}">{esc(_title(g.name))}</a>' for g in found.remixes)
        more = f" and {found.count - len(found.remixes)} more" if found.count > len(found.remixes) else ""
        lines.append(f"<p>Remixed {times(found.count)}: {links}{more}</p>")
    if found.fork_url:
        lines.append(
            f'<p><a class="ghost btn" href="{esc(found.fork_url)}" rel="noopener">Remix it</a> '
            "Make your own version: fork it on GitHub, change the name, make it yours, then publish it on start.ng.</p>"
        )
    if not lines:
        return ""
    return f'<section class="game-remix" aria-label="Remix"><span>Remix</span>{"".join(lines)}</section>'


GAME_PAGE_PARTS.append(game_page_part)
