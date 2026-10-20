# startng-be

FastAPI backend for start.ng. Postgres + SQLAlchemy + Alembic. Email/password and Google sign-in (returns a JWT).

## Endpoints
- `GET /health`
- `POST /auth/register`, `POST /auth/login`, `POST /auth/google` (`{id_token}`), `GET /auth/me`
- `PUT /auth/me/character`: save the signed-in user's character (`{name, style}`); `GET /auth/me` returns it as `character`
- `POST /games/publish`: `{repo_url, name?, branch?}` publishes a game from a public GitHub repo at `<name>.start.ng`.
  Signed in, it goes live on your account. Without signing in, a repo that's already live updates straight away; a new
  one is checked and returns **202** with an `approve_url` (`<SITE_URL>/approve/<code>`): whoever opens it signs in
  with start.ng and approves, and it goes live on their account, linking the repo to it for good. Unapproved ones
  expire after 30 days; new ones are limited to 20 an hour per address
- `POST /games/check`: `{repo_url, name?, branch?}` runs every publish check without publishing: 200 with `warnings`, or 422
  with every problem. Publish and check refuse with `{ok: false, detail, errors: [{code, message, file}], warnings}`
  (`app/checks.py` has the content checks: malware signatures, miners, hidden or obfuscated code, password fields,
  redirects, disguised programs). A failed auto-update keeps the last version and shows the owner why in `/games/mine`
- `GET /games/publish/{code}`: what's waiting at an approve link; `POST /games/publish/{code}/approve` (signed in): put it live
- `GET /games`: published games; `DELETE /games/{name}` (owner only): take a game offline
- `GET /games/mine` (signed in): your own games, newest first (the profile's "Your games")
- `POST /games/{name}/scores` (signed in): `{score}` saves a score; only each player's best per game is kept.
  Returns `{best, improved, rank}`
- `GET /games/{name}/leaderboard?limit=10`: a game's top players `[{rank, player_id, name, color, score}]`.
  Games don't call these directly: they ask start.ng's `/connect` page, which adds the player's sign-in
- `POST /games/{name}/ticket` (signed in): a one-minute ticket into the game's live room (games get it through `/connect`).
  A ticket only opens that room; it never works as a sign-in
- `WS /games/{name}/live?ticket=…`: the live room for multiplayer games (e.g. the Hangout). Players see each other
  join, move, wave and chat; names and looks come from the database. A room remembers its last 50 chat
  messages for players who come in later. Messages are described in `app/live.py`
- `PUT /games/{name}/cover` (owner only): the picture as the raw request body (PNG, JPEG or WebP, up to 2 MB)
  sets the game's card picture; `DELETE /games/{name}/cover` removes it. Both return the game.
  `GET /games` and `/games/mine` give each game's `cover_url` (`null` = no picture)
- `GET /tournaments/current`: today's tournament (games, live standings, whether you've entered), or `null`
  when fewer than 3 games qualify. `POST /tournaments/current/enter` (start.ng sign-in, not a game session)
  enters it. `GET /tournaments/recent?limit=7`: finished tournaments with their winner and top 3.
  `GET /tournaments/leaderboard?period=all|week&limit=50`: the overall leaderboard. See "Daily tournaments"

## Building a feature (start here)
Every new feature lives in its own folder with one owner, so nobody edits the same files (the PRD, section 3).
1. Copy `app/features/_template` to `app/features/<feature>/` and rename things. It has `router.py`, `models.py`,
   `schemas.py`, `service.py` and `tests/`, with comments.
2. Add **one line** to `FEATURES` in `app/features/__init__.py`. That's the only shared edit:
   - `main.py` mounts your router behind your flag;
   - alembic sees your tables;
   - the admin page offers your `PERMISSIONS`.
3. **Flags:** your routes answer 404 until your `NAME` is in `FEATURES` (env, comma separated; `*` turns everything
   on locally). `GET /features` lists what's on, for the website.
4. **Events** (`app/events.py`): `events.publish(name, payload)` after you commit, and `@events.subscribe(name)` to
   react. Every event and its payload is listed in `EVENTS`; add yours there. Handlers that fail are logged, never
   raised. Published today: `game_published`, `game_updated` (`auto` = from a push), `tournament_finished`.
5. **startng.json** (`app/manifest.py`): `manifest.register_section("category", parse, apply)`. `parse` checks your
   key (raise `SchemaError` to refuse the publish), and `apply(db, game, value)` saves it when the game goes live.
6. **Activity log:** `app.activity.record(db, user, "player", "<feature>.<what>", target)` shows it to admins.
7. **Hiding a game:** `app.admin.hide_game(db, store, game, reason)` and `show_game(...)` hide it everywhere (lists,
   pages, its own address, tournaments) and bring it back. A feature's own reason: `register_hide_reason("...")`.
8. **Migrations** start at `0019`; renumber right before merging. Tests: `app/features/<feature>/tests`, run by
   `pytest` with the rest.

## Admin page and reports
`app/admin.py`, `app/routers/admin.py`; the website's page is start.ng/admin.
- **Who:** super admins are `SUPER_ADMIN_EMAILS` (every permission; only they add admins and make roles). Other admins
  are added by email with a role: a named set of permissions (`GET /admin/permissions` lists them). There's no admin
  sign-up: admins sign in to start.ng with Google as that email. An account with a password never gets admin access,
  because registering with a password doesn't prove the email is yours. Non-admins get 404 from every `/admin` call.
- **Reports:** `POST /games/{name}/report` (signed in) `{reason, details?}` with reason `broken`, `obscene`, `offensive`,
  `harmful`, `stolen` or `other` (details needed). The game stays up until an admin approves the report
  (`POST /admin/reports/{id}/close` `{outcome: "approved" | "dismissed"}`); approving hides it (not in `/games`, its page
  or the sitemap, swapped out of tournaments that haven't started and dropped from the running one, and its own address `<name>.start.ng` shows an
  error page: the game store writes `_hidden/<name>` and the Worker checks it). One open report per player per game,
  `REPORTS_PER_DAY` (10) a day. Owners see `hidden` on their games in `/games/mine`.
- **Admins:** `GET /admin/games/review` (games with reports waiting, then hidden games), `POST /admin/games/{name}/flag`
  (hides at once), `POST /admin/games/{name}/restore`.
- **Developers:** admins record each time they reach out (`POST /admin/developers/{id}/contacts`). After
  `CONTACTS_BEFORE_BLOCK` (3) issues they can block one (`/block`: can't sign in or publish, games hidden; `/unblock`)
  or delete one with all their games (`DELETE /admin/developers/{id}`). Admins can't be blocked or deleted.
- **Tournaments:** `GET /admin/tournaments` gives the current tournament and the next one (made ahead of time, so admins
  see it first) as game cards, and every eligible game. `PUT /admin/tournaments/{id}/games` or `POST .../reroll` change
  one that hasn't started. Players get `next_starts_at` in `/tournaments/current` for a countdown, not the next games.
- **Activity:** `GET /admin/log?kind=player|developer|admin|system` is everything that happens on start.ng: accounts
  made, games published, updated, auto-updated or removed, covers, Creators listings, reports, tournaments made,
  entered and finished, and every admin action (`app/activity.record`).

## Daily tournaments
`app/tournaments.py`. Every day a tournament of 5 random games runs for 24 hours and ends at 18:00 Nigeria time
(`TOURNAMENT_GAMES`, `TOURNAMENT_MIN_GAMES`, `TOURNAMENT_END_HOUR`, `TOURNAMENT_UTC_OFFSET`).
- **Games:** ones with a leaderboard (a `startng.json`, or saved scores) that don't fail the game standards, never the
  Hangout, and not ones from the last 3 tournaments while there are enough others. Fewer than 3: no tournament.
  `games.meets_standards` is saved on every publish and update; games published before the standards (`null`) can
  still be picked for now.
- **Automatic:** nothing runs on a timer. The day's tournament is made the first time anyone asks for it, and one
  that has ended is settled (points saved, winner named) the next time anyone reads tournaments.
- **Entering:** the website enters players when they share the tournament on X (it can't check the post).
- **Scores:** games need no changes. While a player has entered, every run sent to `POST /games/{name}/scores` for a
  tournament game also counts there; only runs inside the 24 hours count, and only the best one per game.
- **Points:** on each game, 10 / 7 / 5 / 3 for the top four, 1 for everyone else who played it. Most points wins
  the day; ties go to more first places, then whoever got there first. Points only come from tournaments.
  The overall leaderboard adds them up, all time or this week (from Monday 00:00 Nigeria time).

## Keeping games up to date
- While the server runs it checks each game's branch on GitHub and republishes the game when there's a new commit
  (`app/auto_update.py`), so a push is all a builder needs to do. Games updated in the last day are checked every
  minute, the last 30 days every 5 minutes (`UPDATE_CHECK_SECONDS`), older ones hourly
- Each check asks only for the latest commit id and sends GitHub's fingerprint (ETag) of the last answer, so an
  unchanged repo costs nothing. Set `GITHUB_TOKEN` (read-only, public repos) for 5,000 checks an hour instead of 60
- A commit that doesn't publish (no `index.html`, too big, ...) is logged and skipped; the live game stays as it was
- A game with no recorded commit (published before auto-update existed, or its lookup failed) is republished the first time it's checked, once, so it can't stay stale. At most 5 games are downloaded per 30-second pass
- `GET /games/mine` also gives the owner `checked_at`, `live_commit` (7 characters) and `updates_paused_until` (set while GitHub's limit has paused checking), shown on each game card in Your games
- `AUTO_UPDATE=false` turns it off (tests do)

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
