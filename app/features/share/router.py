"""Share cards' API (B2): the link, text and picture for something (GET /share/{kind}/{id}), the picture itself
(.../card.png?size=wide|square) and the page behind /s/<kind>/<id> (.../page) whose tags make a pasted link show the
card and which sends people on to the thing itself. All 404 while the flag is off."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...db import get_db
from ...routers import pages
from . import render, service

router = APIRouter(prefix="/share", tags=["share"])

KIND = Path(pattern=r"^[a-z_]{1,30}$")
ITEM = Path(pattern=r"^[A-Za-z0-9_.-]{1,40}$")


class SharedOut(BaseModel):
    url: str
    text: str
    image_url: str
    square_image_url: str


def _card(db: Session, kind: str, item: str) -> service.Card:
    card = service.describe(db, kind, item)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to share here")
    return card


@router.get("/{kind}/{item}", response_model=SharedOut)
def share(kind: str = KIND, item: str = ITEM, db: Session = Depends(get_db)):
    made = service.make(db, kind, item)
    if made is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to share here")
    return SharedOut(**made.__dict__, square_image_url=service.image_url(kind, item, "square"))


@router.get("/{kind}/{item}/card.png", response_class=Response)
def card_png(kind: str = KIND, item: str = ITEM, size: str = Query("wide", pattern="^(wide|square)$"), db: Session = Depends(get_db)):
    card = _card(db, kind, item)
    png = render.picture(f"{kind}/{item}", card, size)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


@router.get("/{kind}/{item}/page", response_class=Response)
def preview_page(kind: str = KIND, item: str = ITEM, db: Session = Depends(get_db)):
    """The website's HTML with the card's title, description and picture in its tags (nginx asks for it on /s/...),
    and a redirect to the thing itself for people."""
    shell = pages._get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    card = service.describe(db, kind, item)
    path = f"/s/{kind}/{item}"
    if card is None:
        body = '<main><h1>Nothing here</h1><p>This link doesn\'t lead anywhere any more.</p><p><a href="/">Go to start.ng</a></p></main>'
        page = pages._page(shell, title="Nothing here · start.ng", description="This link doesn't lead anywhere any more.", path=path,
                           image=pages._site() + pages.SHARE_IMAGE, body=body, noindex=True)
        return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
    target = card.target if card.target.startswith("/") and not card.target.startswith("//") else "/"
    description = card.description or card.subtitle or card.title
    refresh = f'<meta http-equiv="refresh" content="0;url={pages._esc(target)}" />'
    body = f'<main><h1>{pages._esc(card.title)}</h1><p>{pages._esc(description)}</p><p><a href="{pages._esc(target)}">Open on start.ng</a></p></main>'
    page = pages._page(shell, title=f"{card.title} · start.ng", description=description, path=path,
                       image=service.image_url(kind, item), body=body, noindex=True, extra_head=refresh)
    return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
