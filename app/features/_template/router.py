from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...activity import record
from ...db import get_db
from ...models import User
from ...security import current_user
from .models import ExampleNote
from .schemas import NoteIn, NoteOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/example", tags=["example"])


@router.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def add_note(body: NoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    note = ExampleNote(user_id=user.id, text=body.text)
    db.add(note)
    record(db, user, "player", "example.note_added")  # shows in the admin page's activity log
    db.commit()
    return NoteOut(id=note.id, text=note.text)
