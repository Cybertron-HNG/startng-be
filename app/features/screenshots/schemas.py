from pydantic import BaseModel


class Screenshot(BaseModel):
    url: str  # on the game's own address (<name>.start.ng), served by the Worker
    alt: str


class ScreenshotsOut(BaseModel):
    screenshots: list[Screenshot]
