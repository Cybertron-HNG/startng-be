"""The laptop and phone line on a game's server-written page (start.ng/games/<name>), so search engines read what people see."""
from sqlalchemy.orm import Session

from ... import flags
from ...routers.pages import GAME_PAGE_PARTS
from . import service


def game_page_part(db: Session, game) -> str:
    """The same words and classes as src/features/discovery/DeviceIcons.tsx on the game's page."""
    if not flags.is_on(service.NAME):
        return ""
    ready = service.is_mobile_ready(db, game.name)
    phone = '<span class="device">Mobile ready</span>' if ready else '<span class="device off">Not mobile ready</span>'
    return f'<p class="device-icons big"><span class="device">Works on laptop</span> {phone}</p>'


GAME_PAGE_PARTS.append(game_page_part)
