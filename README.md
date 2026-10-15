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
- `POST /games/{name}/ticket` (signed in): a one-minute ticket into the game's live room (games get it through `/connect`).
  A ticket only opens that room; it never works as a sign-in
- `WS /games/{name}/live?ticket=…`: the live room for multiplayer games (e.g. the Hangout). Players see each other
  join, move and wave; names and looks come from the database. Messages are described in `app/live.py`
- `PUT /games/{name}/cover` (owner only): the picture as the raw request body (PNG, JPEG or WebP, up to 2 MB)
  sets the game's card picture; `DELETE /games/{name}/cover` removes it. Both return the game.
  `GET /games` and `/games/mine` give each game's `cover_url` (`null` = no picture)

## Game covers
- An uploaded cover is opened, cropped to 640 x 400 and saved again as WebP (`app/covers.py`), so nothing
  hidden in the file is kept. It goes in the game's folder as `startng-cover.webp` and the Worker serves it.
- Without an upload, a `cover.png` / `.jpg` / `.jpeg` / `.webp` at the top of the published game is used.
  An uploaded cover wins, and republishing never replaces it.
- `cover_url` ends in `?v=<time>`, which changes with the cover, so nobody sees an old picture.

## The Hangout
start.ng's own multiplayer game runs inside the front end at `/hangout`, but its live room needs a `hangout`
row in `games`. Migration `0007` adds it, owned by a start.ng system account (`system@start.invalid`) that
nobody can sign in to, so the Hangout belongs to no person. It runs with the rest of the migrations on deploy.
If someone already published a game called `hangout`, it is left alone. `GET /games` lists it as by "start.ng".

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

## API for games (game sessions)
Games on `<name>.start.ng` call the API directly (CORS allows `*.start.ng`, and `*.start.localhost` for testing).
They never get the player's sign-in token. Instead start.ng gives them a **game session**: a key that only works
for that game's player API, expires after 2 hours, and is never a sign-in.

- `POST /games/{name}/session` (start.ng sign-in): `{session, expires_in, player}`. start.ng calls this and hands
  the session to the game (sign-in redirect `#startng_session=...`, or `/connect`).
- `GET /games/{name}/player` (game session): `{id, name, style}`, never the email.
- `POST /games/{name}/scores` (game session, or a start.ng sign-in): `{score}` → `{best, improved, rank}`.
- `GET /games/{name}/leaderboard?limit=10` (public): `[{rank, player_id, name, color, score}]`.

A session for one game is refused by every other game and by `/auth/*`.

### Game-defined leaderboards (opt-in)
A game gets a leaderboard by shipping a `startng.json` (in its game folder, or at the top of the repo), read on publish:
```json
{"leaderboard": {"stats": [
  {"key": "waves", "label": "Waves survived", "order": "highest"},
  {"key": "kills", "label": "Enemies killed"},
  {"key": "time", "label": "Time", "order": "lowest", "format": "time"}
]}}
```
- 1–5 stats; `key` lowercase letters/digits/_ ; `order` `highest` (default) or `lowest`; `format` `number` (default) or `time` (seconds).
- Ranked by the first stat, then the next ones break ties, then whoever got there first. Each player's best run is kept.
- Games send `POST /games/{name}/scores` `{"stats": {"waves": 5, "kills": 10, "time": 312}}`: exactly the declared stats,
  whole numbers 0–1,000,000,000. Nothing else is stored (no text).
- `GET /games` returns each game's `leaderboard` columns, or `null` when it has none (no `startng.json` and no scores).
  Games without a `startng.json` keep the plain `{"score": n}` board.
- `DELETE /games/{name}/scores/{player_id}` (owner): remove a player's score.

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
