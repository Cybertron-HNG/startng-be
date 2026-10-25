"""F1: GitHub's webhook for the start.ng GitHub App (POST /github/webhook) and what Your games needs to link to it.
404 while the github_app flag is off."""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...config import settings
from ...db import get_db
from . import service

log = logging.getLogger(__name__)
router = APIRouter(tags=["github app"])


class AppInfo(BaseModel):
    install_url: str | None  # where an owner installs the app on their repo; None until the app is set up


@router.get("/github/app", response_model=AppInfo)
def app_info():
    slug = settings.github_app_slug.strip()
    return AppInfo(install_url=f"https://github.com/apps/{slug}/installations/new" if slug else None)


@router.post("/github/webhook", status_code=status.HTTP_202_ACCEPTED)
async def webhook(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """GitHub calls this for the app's events. Only a correctly signed request is read. A push to a game's branch
    updates that game in the background: GitHub waits only 10 seconds for an answer."""
    if not settings.github_webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The GitHub App isn't set up")
    body = await request.body()
    if not service.signature_ok(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return JSONResponse({"ok": True, "updating": []}, status_code=status.HTTP_200_OK)
    if event != "push":
        return {"ok": True, "updating": []}  # installations and the like: nothing to do
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not JSON")
    todo = service.games_for_push(db, payload)
    for name, sha in todo:
        background.add_task(service.update, name, sha)
    return {"ok": True, "updating": [name for name, _ in todo]}
