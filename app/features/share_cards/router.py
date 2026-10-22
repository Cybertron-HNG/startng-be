from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...routers import pages
from . import render, service
from .schemas import ShareOut

# main.py mounts this with the feature's flag: every route answers 404 while the flag is off.
router = APIRouter(prefix="/share", tags=["share"])

KIND = Path(pattern=r"^[a-z_]{1,32}$")
ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")


def _card(db: Session, kind: str, id: str) -> service.Card:
    card = service.describe(db, kind, id)
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to share at this link")
    return card


@router.get("/{kind}/{id}", response_model=ShareOut)
def share_info(kind: str = KIND, id: str = ID, db: Session = Depends(get_db)):
    """The link, words and pictures for sharing one thing (what the website's Share button posts)."""
    card = _card(db, kind, id)
    s = service.links(kind, id, card)
    return ShareOut(url=s.url, text=s.text, image_url=s.image_url, square_image_url=s.square_image_url,
                    title=card.headline, path=card.path)


@router.get("/{kind}/{id}/card.jpg", response_class=Response)
def card_image(kind: str = KIND, id: str = ID, size: str = Query("wide", pattern="^(wide|square)$"),
               db: Session = Depends(get_db)):
    """The card picture. Its address carries ?v=<version>, so it can be cached for a long time."""
    jpg = render.draw(_card(db, kind, id), size)  # a plain def: FastAPI runs it off the event loop
    # Allowed from anywhere: a browser that cached it from an <img> (no CORS header) reuses that copy when the Share
    # button fetches it to attach on a phone, and would block it. It's public and needs no sign-in.
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})


@router.get("/{kind}/{id}/page", response_class=Response)
def preview_page(kind: str = KIND, id: str = ID, db: Session = Depends(get_db)):
    """start.ng/s/<kind>/<id> with the card in its preview tags (nginx asks for it). Chat apps read the tags; people
    are sent on to the card's own page by the website."""
    shell = pages._get_shell()
    if shell is None:
        return Response("The page isn't available right now", status_code=503, media_type="text/plain")
    path = f"/s/{kind}/{id}"
    card = service.describe(db, kind, id)
    if card is None:
        body = '<main><h1>Nothing here</h1><p>This link has expired or was removed.</p><p><a href="/">Play games on start.ng</a></p></main>'
        page = pages._page(shell, title="Not found · start.ng", description="This link has expired or was removed.",
                           path=path, image=pages._site() + pages.SHARE_IMAGE, body=body, noindex=True)
        return Response(page, status_code=404, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
    s = service.links(kind, id, card)
    description = f"{card.headline}. {card.cta}".strip() if card.cta else card.text
    body = (f"<main><h1>{pages._esc(card.headline)}</h1><p>{pages._esc(description)}</p>"
            f'<p><a href="{pages._esc(card.path)}">Open it on start.ng</a></p></main>')
    extra = "\n    ".join([
        '<meta property="og:image:width" content="1200" />',
        '<meta property="og:image:height" content="630" />',
        f'<meta property="og:image:alt" content="{pages._esc(card.headline)}" />',
    ])
    page = pages._page(shell, title=f"{card.headline} · start.ng", description=description, path=path, image=s.image_url,
                       body=body, noindex=True, extra_head=extra)
    return Response(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=60"})
