import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from . import auto_update, features, flags, security
from .config import settings
from .db import get_db
from .errors import ApiError, api_error_handler
from .routers import admin, auth, favorites, games, indie, live, pages, tournaments

# Show the app's own messages (e.g. "Updated lane-dash to ...") next to uvicorn's.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keeps live games in step with their GitHub repos while the server runs.
    tasks = [asyncio.create_task(auto_update.run_forever())] if settings.auto_update else []
    # Features' background loops (TASKS in their __init__.py). Each checks its own flag as it runs.
    tasks += [asyncio.create_task(run()) for feature in features.load() for run in getattr(feature, "TASKS", [])]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Start.ng API", lifespan=lifespan)
app.add_exception_handler(ApiError, api_error_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.game_origins_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Refreshed-Token"],  # the website reads this to keep a sign-in going (see below)
)


@app.middleware("http")
async def renew_sign_in(request: Request, call_next):
    """Every visit keeps a sign-in going: when the token sent is past half of its life and the request worked, the answer carries a
    fresh one in X-Refreshed-Token, and the website swaps it in. Someone who stays away longer than the lifetime (a week, or a year
    if they chose to stay signed in) is signed out."""
    response = await call_next(request)
    auth = request.headers.get("authorization", "")
    if response.status_code < 400 and auth[:7].lower() == "bearer ":
        make_db = request.app.dependency_overrides.get(get_db, get_db)  # the same database the request used (tests replace it)
        gen = make_db()
        try:
            db = next(gen)
            fresh = await run_in_threadpool(security.renewed_token, auth[7:], db)
        finally:
            gen.close()
        if fresh:
            response.headers["X-Refreshed-Token"] = fresh
    return response

app.include_router(auth.router)
app.include_router(admin.reports_router)  # before games.router: POST /games/{name}/report
app.include_router(games.router)
app.include_router(live.router)
app.include_router(indie.router)
app.include_router(favorites.router)
app.include_router(pages.router)
app.include_router(tournaments.router)
app.include_router(admin.router)

# Features (app/features): each one's routes answer 404 until its flag is on (FEATURES, app/flags.py).
for feature in features.load():
    if feature.router is not None:
        app.include_router(feature.router, dependencies=[Depends(flags.require(feature.NAME))])


@app.get("/features")
def enabled_features():
    """The feature flags that are on, so the website can hide what's off. ["*"] means all of them."""
    return sorted(flags.enabled_features())


@app.get("/health")
def health():
    return {"status": "ok"}
