"""The admin page's Features tab: see every feature, and switch one on or off without a redeploy. Super admins only."""
import importlib

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import admin as adm
from .. import features, flags
from ..admin import AdminUser, require_super
from ..db import get_db
from ..models import FeatureSwitch

router = APIRouter(prefix="/admin/features", tags=["admin"])

# Features that change real behaviour or send things to people the moment they are switched on. The tab asks "are you sure" first.
CAUTION = {
    "moderation": "Starts checking new game names, descriptions and character names, and refuses ones with a blocked word.",
    "integrity": "Starts limiting and flagging scores: a run that looks too good is held for review and a player can be rate limited.",
    "needs_fixing": "Lets admins hide a game until its owner fixes it.",
    "duplicates": "Starts fingerprinting games; admins can then hide or delete copies.",
    "email": "Lets the server send email. Check the SMTP settings and the warm-up plan first.",
    "game_health": "Starts checking every live game about every 20 minutes. It only reports.",
    "wardrobe": "Starts giving coins and items, and opens the shop.",
    "private_tournaments": "Lets players make private tournaments with an invite link.",
}


class FeatureOut(BaseModel):
    name: str
    description: str
    enabled: bool
    source: str  # "admin": switched here; "server": the FEATURES setting
    server_on: bool  # what the FEATURES setting says on its own
    caution: str | None
    changed_by: str | None
    changed_at: str | None


class SwitchIn(BaseModel):
    enabled: bool | None  # null: back to what the FEATURES setting says


def _description(name: str) -> str:
    """The first line of the feature's own description (its package docstring)."""
    try:
        doc = (importlib.import_module(f"app.features.{name}").__doc__ or "").strip()
    except Exception:
        doc = ""
    first = doc.split("\n\n")[0].replace("\n", " ").strip()
    return first[:220] if first else name.replace("_", " ").capitalize()


def _rows(db: Session) -> list[FeatureOut]:
    mine = {r.feature: r for r in db.scalars(select(FeatureSwitch))}
    out = []
    for name in features.FEATURES:
        row = mine.get(name)
        out.append(FeatureOut(
            name=name, description=_description(name), enabled=row.enabled if row else flags.env_on(name),
            source="admin" if row else "server", server_on=flags.env_on(name), caution=CAUTION.get(name),
            changed_by=row.changed_by if row else None, changed_at=row.changed_at.isoformat() if row else None,
        ))
    return out


@router.get("", response_model=list[FeatureOut])
def list_features(admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    flags.forget()
    return _rows(db)


@router.put("/{name}", response_model=list[FeatureOut])
def switch(name: str, body: SwitchIn, admin: AdminUser = Depends(require_super), db: Session = Depends(get_db)):
    if name not in features.FEATURES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No feature with that name")
    row = db.get(FeatureSwitch, name)
    if body.enabled is None:
        if row is not None:
            db.delete(row)
        adm.log(db, admin, "feature.reset", name)
    else:
        if row is None:
            row = FeatureSwitch(feature=name, enabled=body.enabled, changed_by=admin.email)
            db.add(row)
        else:
            row.enabled, row.changed_by = body.enabled, admin.email
        adm.log(db, admin, "feature.on" if body.enabled else "feature.off", name)
    db.commit()
    flags.forget()  # this process sees it at once; other processes within CACHE_SECONDS
    return _rows(db)
