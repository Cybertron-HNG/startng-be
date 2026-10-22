from pydantic import BaseModel


class ShareOut(BaseModel):
    url: str  # start.ng/s/<kind>/<id>: what goes in the post
    text: str  # ready-made words for the post
    image_url: str  # 1200x630
    square_image_url: str  # 1080x1080, for attaching on phones (WhatsApp, Instagram)
    title: str
    path: str  # where the link takes people on start.ng
