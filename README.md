# startng-be

FastAPI backend for start.ng. Postgres + SQLAlchemy + Alembic. Email/password and Google sign-in (returns a JWT).

## Endpoints
- `GET /health`
- `POST /auth/register`, `POST /auth/login`, `POST /auth/google` (`{id_token}`), `GET /auth/me`
- `PUT /auth/me/character`: save the signed-in user's character (`{name, style}`); `GET /auth/me` returns it as `character`
- `POST /games/publish` (signed in): `{repo_url, name?, branch?}` publishes a game from a public GitHub repo at `<name>.start.ng`
- `GET /games`: published games; `DELETE /games/{name}` (owner only): take a game offline
- `GET /games/mine` (signed in): your own games, newest first (the profile's "Your games")
- `POST /games/{name}/scores` (signed in): `{score}` saves a score; only each player's best per game is kept.
  Returns `{best, improved, rank}`
- `GET /games/{name}/leaderboard?limit=10`: a game's top players `[{rank, player_id, name, color, score}]`.
  Games don't call these directly: they ask start.ng's `/connect` page, which adds the player's sign-in

## Publishing games
`POST /games/publish` downloads the repo as a zip from GitHub (nothing in it is run), finds a ready-to-play
`index.html` (in `dist/`, `build/`, `docs/`, `out/` or the top), checks it and uploads it to `<name>/` in the bucket.

- Client-side games only. A repo that still needs building (its `index.html` loads `/src/*.ts(x)`) is refused:
  builders commit their built files.
- Names and paths use `app/storage.py`'s rules (the Worker's), and names are at most 40 characters.
  The first person to publish a name owns it (`games` table); only they can update or remove it.
  Folders already in the bucket with no owner (like `dave/`) count as taken.
- Hidden files, `node_modules` and links are left out, file types the Worker doesn't serve are skipped,
  and games are limited to 2000 files and 100 MB (25 MB a file).
- Game files are checked against `malware-patterns.txt`, the same signatures as the CI gate.
- Files go to the bucket through `app/storage.py` (see below). Files an older version had and a new one
  dropped stay in the bucket; the Worker only serves what the game links to.
- Trying it without the R2 keys: set `LOCAL_GAMES_DIR` and games are written to that folder, which the
  Worker's `dev-server.js` can serve. With neither set, publishing answers 503.

Tests: `pip install -r requirements-dev.txt`, then `pytest`.

## Games bucket (R2)
`app/storage.py` reads and writes the private R2 bucket (`startng-games`) that the start.ng Worker serves.
A game named `dave` is stored under `dave/` and served at `https://dave.start.ng`.
```python
from app import storage
storage.put_game("dave", {"index.html": b"<h1>hi</h1>", "save/index.html": b"..."})
storage.list_files("dave"); storage.get_file("dave", "index.html"); storage.delete_game("dave")
```
Names and paths follow the Worker rules (lowercase letters/digits/hyphens, no `..`, no hidden files, reserved
names refused). Needs `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (see `.env.example`).

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Deploy (Coolify)
Dockerfile build pack, port 8000. The container runs `alembic upgrade head` before starting.
Set the variables from `.env.example`. GitHub secrets: `COOLIFY_WEBHOOK_URL` (optional `COOLIFY_TOKEN`);
the `Deploy (Coolify)` workflow runs only after `Malware Scan (CI Gate)` passes on `main`.
