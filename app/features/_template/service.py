"""What other features may call (this feature's contract). Keep it small and documented; never import another
feature's models, call its service instead."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import events
from .models import ExampleNote


def notes_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(ExampleNote).where(ExampleNote.user_id == user_id)) or 0


@events.subscribe("game_published")
def on_game_published(payload: dict) -> None:
    """React to another part of start.ng. Open your own session here if you need the database."""
