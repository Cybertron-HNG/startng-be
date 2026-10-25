import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auto_update, features, flags
from .config import settings
from .errors import ApiError, api_error_handler
from .routers import admin, auth, favorites, feature_switches, games, indie, live, pages, tournaments

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
)

app.include_router(auth.router)
app.include_router(admin.reports_router)  # before games.router: POST /games/{name}/report
app.include_router(games.router)
app.include_router(live.router)
app.include_router(indie.router)
app.include_router(favorites.router)
app.include_router(pages.router)
app.include_router(tournaments.router)
app.include_router(admin.router)
app.include_router(feature_switches.router)

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
