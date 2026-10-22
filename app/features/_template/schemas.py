from pydantic import BaseModel, Field


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class NoteOut(BaseModel):
    id: int
    text: str
