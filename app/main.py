import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auto_update
from .config import settings
from .errors import ApiError, api_error_handler
from .routers import auth, explore, games, live

# Show the app's own messages (e.g. "Updated lane-dash to ...") next to uvicorn's.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keeps live games in step with their GitHub repos while the server runs.
    task = asyncio.create_task(auto_update.run_forever()) if settings.auto_update else None
    yield
    if task:
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
app.include_router(games.router)
app.include_router(explore.router)
app.include_router(live.router)


@app.get("/health")
def health():
    return {"status": "ok"}
