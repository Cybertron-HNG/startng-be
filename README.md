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
- `GET /share/{kind}/{id}` (feature `share_cards`): `{url, text, image_url, square_image_url, title, path}` for sharing one
  thing; `GET /share/{kind}/{id}/card.jpg?size=wide|square` is its card (1200x630 or 1080x1080) and `/page` its
  start.ng/s/<kind>/<id> preview page. Features describe their own kind with `share_cards.service.register` and call
  `service.make(db, kind, id)`; see `app/features/share_cards/service.py`
- `POST /challenges` (feature `challenges`, signed in): `{game}` (your best run on it) or `{run_id}` makes a challenge link,
  `start.ng/c/<code>`; the score is read from the run log. `GET /challenges/{code}` (anyone), `POST /challenges/{code}/accept`
  (signed in: your runs from now on count), `DELETE /challenges/{code}` (its maker). Lasts 30 days; a beaten challenge
  publishes `challenge_beaten` and emails the maker (topic `records`)

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
8. **Migrations** start at `0031`; renumber right before merging. Tests: `app/features/<feature>/tests`, run by
   `pytest` with the rest.

## Progress Engine (PRD D1)
`app/features/progress`; flag `progress` (off by default; with it off, scores behave exactly as before and nothing is logged).
- **Run log:** with the flag on, every `POST /games/{name}/scores` is logged in `runs` (not only personal bests), the player's
  day is updated in `activity_days` (Nigeria time), and registered award rules are evaluated. The score response then also
  carries `unlocked: [{kind, key}]`, the awards that run earned (`[]` when none). Nothing else in the response changes.
- **Streaks:** consecutive days with a run. A streak stays alive until the end of the day after the last one played.
  **Freezes:** a streak earns one each time it reaches a multiple of 7 days (up to 2 banked). A missed day spends one
  automatically and the streak carries on (the frozen day adds nothing); with none left it ends the streak. Worked out
  from the daily activity each time, so there is nothing to keep in step.
  **XP:** +10 for the first run of the day, +2 per run, capped at 50 a day. **Level:** `1 + isqrt(xp // 50)`.
- `GET /me/progress` (signed in): `{runs, distinct_games, current_streak, best_streak, streak_freezes, xp, level, tournament_wins}`.
  `GET /me/awards` (signed in): `[{kind, key, earned_at}]`, newest first.
  `GET /players/{id}/progress` (no sign-in): `{runs, distinct_games, current_streak, best_streak, level, tournament_wins}`,
  never XP or an email. 404 for an unknown player or one whose page is private (the player profile registers a check
  with `progress.register_visibility(feature, lambda db, user_id: ...)`).
- **Game opens (C4):** `POST /games/{name}/plays` with a sign-in token also remembers the open in `game_opens`
  (`first_at`, `last_at`, `count`; read it with `progress.game_open(db, user_id, game)`). Without a token it only counts
  the play, as before. **The website must send the player's token on that call for opens to be recorded.**
- **Tournament link:** a run records the daily tournament it counted for (`runs.tournament_id`).
- **Retention:** runs are kept `RUNS_RETENTION_DAYS` (180); older ones are deleted, at most once an hour per server
  process. Daily activity, streaks, XP and awards are kept.
- **Backfill:** players who scored before this existed get one run per game they have a score in, so "tried 5 games"
  works for them: run `python -m app.features.progress.backfill` once after deploying (safe to repeat). It gives no
  streak and no XP, because the real days are unknown.
- **For other features:** `from app.features.progress import service as progress`, then
  `progress.snapshot(db, user_id)` and `progress.register("wardrobe", [progress.Rule("item", "explorer_cap", lambda snap, run: snap.distinct_games >= 5)])`
  from your own `rules.py`. A rule gets the player's snapshot (after the run) and the run; awards are unique per
  (player, kind, key); a rule that raises is logged and skipped. Events: `run_recorded`, `award_unlocked`.

## Wardrobe: coins, items and the shop
`app/features/wardrobe`; flag `wardrobe` (needs `progress`; `integrity` gives the ranks that the top 10 and #1 rewards use).
Players earn **coins** and **items** by playing any start.ng game, and items show on their one character everywhere.
Coins are earned only: nobody can buy them with money.
- **Coins** (`rewards.py`): first run of the day 10 and each game you've never played 25, capped with the game goals at
  `DAILY_CAP` (300) a day; uncapped: 3/7/30-day streaks 30/100/500, each new level x10, top 10 on a game 150 and #1 400
  (once per game, scaled down below 30 players, nothing for the top 10 of 10 or fewer), a daily tournament's top 3
  300/200/100 (scaled down below 10 players), beating a challenge 50, a league promotion 75.
  Every change is a row in `coin_ledger` with its reason, unique per (player, reason, ref), so nothing is paid twice;
  the balance in `wallets` can't go below zero. Held runs (integrity) earn nothing until an admin clears them.
- **Game goals** (`goals.py`): a game lists up to 10 in its startng.json, on its leaderboard stats. start.ng sets what
  each pays from how hard it is: the share of the game's players whose best run meets it (50%+ easy 10, 20%+ medium 25,
  5%+ hard 50, 1%+ very hard 100, fewer legendary 150; under 20 players "new", 20). Daily goals pay half and can be met
  once a day. At most 150 coins a day from one game's goals. A `coins` key is ignored.
  `{"rewards": {"goals": [{"key": "rich", "label": "Earn 20,000", "stat": "naira", "at_least": 20000},
  {"key": "fast", "label": "Under 2 minutes", "stat": "time", "at_most": 120, "repeat": "daily"}]}}`
- **Items** (`catalog.py`): six rarities. Common, uncommon and rare are in the shop (100 to 5,000 coins); epic, legendary
  and limited can never be bought. Earned ones: explorer cap (5 games), streak scarf (7 days), flame aura (30 days) and
  veteran jacket (level 10) are Progress Engine rules (`rules.py`, so they show in the score reply's `unlocked`); silver
  crown (top 10) and gem crown (#1) need a board of 30 or more players; the tournament cape and champion's sash come
  from winning a tournament and beating a challenge. Item keys never reuse a free character-creator value.
- **Shop:** today's stock changes at midnight Nigeria time; 4 featured items are 20% off. Limited items sell only
  between their dates.
- **Wearing:** one item per slot (`hat`, `accessory`, `outfit`, `neck`, `back`, `aura`). `service.style_overrides`
  gives games the `hat`/`accessory` values to use and every worn item in `items`; games fall back on unknown values.
  Live rooms (the Hangout) already send each player's look with what they wear (`routers/live.py`).
- **API:** `GET /wardrobe/items` (every item, its price today or `null`, `owned_share`), `GET /shop`,
  `POST /shop/buy {item}`, `GET /me/wallet` (balance, coins from playing today, the cap, the last 50 changes),
  `GET /me/wardrobe`, `POST /me/wardrobe/wear {item}`, `POST /me/wardrobe/take-off {slot}`,
  `GET /players/{id}/wardrobe` (worn items and the three rarest; 404 when the profile is private),
  `GET /games/{name}/goals` (each goal's tier, coins and how many players reach it).
  Admin (`wardrobe.manage`): `POST /admin/wardrobe/coins {user_id, amount, note}` (negative takes back, never below
  zero) and `POST /admin/wardrobe/items {user_id, item}`.
- **Events:** publishes `coins_earned` and `wardrobe_item_unlocked`; listens to `run_recorded`, `tournament_finished`,
  `challenge_beaten`, `league_promoted` and `award_unlocked`.

## Score integrity and fast leaderboards (PRD D2)
`app/features/integrity`; flag `integrity` (off by default; with it off nothing below applies and scores work as before).
Limits, a rate limit, the leaderboard layer, and outlier flags with a review queue.
- **Limits:** a game can set `"max"` on a stat in its startng.json (`{"key": "score", "label": "Score", "max": 5000}`, a whole
  number from 1 to 1,000,000,000). A run with a stat above it is refused with 422 and the code `score_out_of_range`
  (`{"ok": false, "detail": ..., "errors": [{"code": "score_out_of_range", ...}]}`) and nothing is saved.
- **Rate limit:** at most `SCORES_PER_MINUTE` (30) scores per player per game in any 60 seconds; over it, 429 with
  `rate_limited` and a `Retry-After` header. Kept in memory per server process.
- **Leaderboards:** same ranking as before (first stat, then the next ones, then whoever got there first, then who was saved
  first) but sorted in the database, so it stays quick with tens of thousands of scores. `GET /games/{name}/leaderboard`
  keeps its output and is cached for 30 seconds; a new or removed score clears that game's cache at once. The rank in a
  score response is always worked out fresh.
- **Outlier flags:** a run is held for review when its first stat is more than `FLAG_PREVIOUS_FACTOR` (3) times the player's
  previous best (when that was at least `FLAG_MIN_PREVIOUS`, 20), or more than `FLAG_PERCENTILE_FACTOR` (5) times the game's
  99th percentile (once the game has `FLAG_MIN_PLAYERS`, 20, players). For a lowest-is-best stat the same tests run the other
  way (a time under a third of their best). A flagged run is **saved in the run log as flagged** but is not the player's best,
  isn't on the board, doesn't count in a tournament (private ones too), earns no XP or awards, and publishes no event. The
  answer to the game looks like any other, so nobody can tell it was held back.
- **Review queue:** the game's owner sees its flags at `GET /games/{name}/flags` (never an email or an admin's note). Admins
  with the new `scores.review` permission use `GET /admin/score-flags?status=open|cleared|confirmed`,
  `POST /admin/score-flags/{id}/clear` (the run counts from now on: it can become the player's best, counts as a run, awards
  are checked; XP is not added back and a finished tournament is not redone) and `POST /admin/score-flags/{id}/confirm`.
  Each decision is in the activity log. **The admin page needs a tab for this in startng-fe.**
- **Rank held:** each run records the player's place afterwards (`runs.rank_after`); `progress.snapshot(...).ranks` gives, per
  game, the worse of the player's last two places, so a rank only counts for rewards once it was held across two runs.
- **`record_beaten`:** published when a run takes the top spot from someone else.
- **For other features (contract C5):** `from app.features.integrity import service as integrity`, then
  `integrity.ranked(db, game, limit, only_users=None)` (the game's best runs, best first, with `.user` loaded) and
  `integrity.rank_of(db, user_id, game)` (a place, or None). `only_users` ranks just those players (friends, a crew).

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

## Name filter (PRD H2)
`app/features/moderation`; flag `moderation`; admin permission `moderation.words`.
- **For other features:** `check_text(text)` returns the blocked word, or `None` when the text is fine.
  `require_clean(db, text, "crew name")` raises a 422 (`text_not_allowed`) that names the field, never the word. Both
  are in `app/features/moderation/service.py` and let everything through while the flag is off. Use them on any name
  people type (crews, wardrobe items, ...).
- **Checked while the flag is on:** new game names, the `startng.json` description (a description start.ng found in
  the page or README is just left out), character names and Creators listings. Live game names aren't touched; admins
  see the ones that fail.
- **Word lists:** built in, in `words.py`. "Strong" words are caught even inside other words; the rest only as whole
  words with simple endings (-s, -es, -ed, -ing). An allow list keeps innocent names (Scunthorpe). Look-alike letters,
  accents, dots and stretched letters (sh1t, $hit, s.h.i.t, shiiit) are undone first. Admins add their own blocked or
  allowed words, saved in `moderation_words`.
- `GET`/`POST /admin/moderation/words`, `DELETE /admin/moderation/words/{id}`, `POST /admin/moderation/try` (check a
  name), `GET /admin/moderation/flagged` (live names that fail).

## Needs fixing (PRD H2)
`app/features/needs_fixing`; flag `needs_fixing`; admin permission `games.needs_fixing`.
- An admin marks a broken game with a note for its owner. The game is hidden everywhere (`admin.hide_game`, reason
  `needs_fixing`) until an admin puts it back.
- The owner sees the note in Your games and says it's fixed (with an optional note), which puts it back in the admins'
  queue. Admins put it back, or send it back with a new note. One open request per game; each step goes to the
  activity log. A game also hidden for a stronger reason (flagged, blocked) stays hidden.
- Admins: `GET /admin/needs-fixing`, `POST /admin/needs-fixing/games/{name}` `{note}`,
  `POST /admin/needs-fixing/{id}/approve`, `POST /admin/needs-fixing/{id}/reopen` `{note}`.
  Owners: `GET /needs-fixing/mine`, `POST /needs-fixing/{id}/fixed` `{note}`.

## Duplicate finder (PRD H2)
`app/features/duplicates`; flag `duplicates`; admin permission `games.duplicates`.
- Every game is fingerprinted when it's published or updated (`game_published`, `game_updated`): its `index.html`
  (spaces ignored), its cover, its list of files and sizes, and its name with "copy", "v2" and trailing numbers taken
  off. `POST /admin/duplicates/scan` fingerprints games published before the feature was on.
- Two games are a pair when they have the same files, the same page or the same cover, or names one letter apart
  (6+ letters). The older game is shown first. The Hangout is never included.
- Admins keep both (the pair stops showing), hide one as a copy (`admin.hide_game`, reason `duplicate`), put it back,
  or delete it for good (files, scores, plays). Owners see which game theirs was hidden as a copy of.
- Admins: `GET /admin/duplicates`, `POST /admin/duplicates/scan`, `POST /admin/duplicates/keep` `{a, b}`,
  `POST /admin/duplicates/games/{name}/hide` `{of}`, `GET /admin/duplicates/hidden`,
  `POST /admin/duplicates/games/{name}/show`, `DELETE /admin/duplicates/games/{name}`. Owners: `GET /duplicates/mine`.

## Email (PRD H1)
`app/features/email`; flag `email`; admin permission `email.view`. Sent with Brevo, behind a small `EmailProvider`
(`provider.py`), so moving to another provider is one new class.
- **For other features (C7):** declare your kind once, then notify:
  ```python
  from app.features.email import service as email

  email.register_kind("record_beaten", "records", lambda d: email.Note(
      subject="Your record was beaten", html=f"<p>{email.esc(d['by'])} beat you.</p>", text=f"{d['by']} beat you."))
  email.notify(user_id, "record_beaten", {"by": "Ada"})        # one player, only if they chose the topic
  email.broadcast("new_games", "weekly_digest", {...})          # everyone on a topic, visitors too
  ```
  Escape anything from players with `email.esc()`. `notify()` does nothing while the flag is off.
- **Topics:** `tournament`, `results`, `records`, `new_games`, `follows`, `newsletter`. Signed-in players choose theirs;
  visitors can ask for `tournament`, `new_games` and `newsletter`, and confirm by a link first (double opt-in).
- **One email a day:** notes wait a few minutes (`EMAIL_MERGE_MINUTES`); notes for the same player that day join the
  waiting email. After it's sent, more notes that day are dropped and logged as `capped`.
- **Every email** has a "choose your emails" link and one-click unsubscribe (a link, plus the `List-Unsubscribe` and
  `List-Unsubscribe-Post` headers).
- **The outbox** (`email_outbox`) keeps every email. A background loop sends what's due every 30 seconds and retries
  failures after 1, 5, 30 and 120 minutes. Bounces and spam complaints from Brevo's webhook mark an address as bad;
  it's never written to again.
- Players: `GET`/`PUT /email/me`. Visitors and email links: `POST /email/subscribe` `{email, topics}`,
  `POST /email/confirm` `{token}`, `POST /email/unsubscribe` (`{token, topic?}`, or `?token=` from mail apps).
  Brevo: `POST /email/webhooks/brevo?key=...`. Admins: `GET /admin/email` (the outbox), `POST /admin/email/test`.
- **Newsletter:** admin permission `newsletter.send` (`newsletter.py`, table `email_newsletters`). An admin writes a
  newsletter (plain text: `# heading`, blank line = new paragraph, `[words](https://link)`), sends themselves a test,
  then sends it to everyone confirmed on the `newsletter` topic, once. Sending less than 14 days after the last one needs
  `force` (the composer asks first). A subscriber who already had today's email gets it tomorrow at 9 AM instead of
  missing it. `GET`/`POST /admin/newsletters`, `PUT`/`DELETE /admin/newsletters/{id}` (drafts only),
  `POST /admin/newsletters/{id}/test`, `POST /admin/newsletters/{id}/send` `{force?}`.
- **Settings:** `EMAIL_PROVIDER` (`log`, the default, writes emails to the server log instead of sending; `brevo`
  or `smtp` sends), `BREVO_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`,
  `SMTP_SSL`, `EMAIL_FROM`, `EMAIL_REPLY_TO`, `EMAIL_ONLY_TO` (while set, only these addresses get email: for
  testing), `BREVO_WEBHOOK_KEY`, `EMAIL_MERGE_MINUTES`.
- **Setting up Brevo:** an API key (SMTP & API, API keys); a verified sender, then the `mail.start.ng` domain with
  the DNS records Brevo lists; the server's IP under Security, Authorised IPs; and a transactional webhook to
  `<API_URL>/email/webhooks/brevo?key=<BREVO_WEBHOOK_KEY>` for hard bounces, spam, blocked, invalid and unsubscribed.

## Game health (PRD H2)
`app/features/game_health`; flag `game_health`; admin permission `games.health`. (Not "health": `GET /health` is the
server's own check.)
- A background loop checks every live game about every 20 minutes (failing ones every 10): `index.html` is there and
  not blank, the scripts and stylesheets it loads from the game's folder exist, and the game's address answers 200.
  Set `GAME_HEALTH_HTTP=false` where the API can't reach the games' addresses (a laptop without the Worker).
- Three failed checks in a row make a game unhealthy and publish `game_unhealthy` `{game, reason}`. One good check
  makes it healthy again, and a new version starts fresh. Unhealthy games aren't hidden: that's an admin's call
  (Needs fixing).
- Admins: `GET /admin/game-health`, `POST /admin/game-health/games/{name}/check`, `POST /admin/game-health/scan`.
  Owners: `GET /game-health/mine` (their unhealthy games), `POST /game-health/games/{name}/check` (once a minute).

## GitHub App: pushes go live straight away (F1)
`app/features/github_app`; flag `github_app`.
- Owners install the start.ng GitHub App on their game's repo (Your games links to it: `GET /github/app`). GitHub then
  calls `POST /github/webhook` on every push, signed with `GITHUB_WEBHOOK_SECRET` (`X-Hub-Signature-256`, checked on
  the raw body). A push to the branch the game is published from (or the repo's default branch) republishes it in the
  background, the same way `app/auto_update.py` does (`auto_update.update_to`): an update that can't go live is shown
  to the owner, and a GitHub or storage hiccup is tried again by the regular check. GitHub sending the same push again
  does nothing (that commit is live already). Checking every few minutes stays for repos without the app.
- **Setting it up (an owner of the GitHub org):** create a GitHub App with permissions Contents: read and Metadata:
  read, subscribed to the Push event, webhook URL `<API_URL>/github/webhook` and a long random webhook secret. Put the
  secret in `GITHUB_WEBHOOK_SECRET` and the app's short name (from github.com/apps/<slug>) in `GITHUB_APP_SLUG`.
  Public repos need no private key.

## Tournament emails (PRD H1)
`app/features/email/tournament_mail.py`, part of the `email` feature.
- At 6 PM one tournament ends and the next starts. Everyone on the `tournament` topic (visitors too) gets who won and
  the new games (`tournament_day`). Each player who chose `results` gets their place and points (`tournament_result`,
  from `tournament_finished`). A player on both gets one email.
- The email loop settles and announces each tournament itself, once (`email_announcements`), within 3 hours of its
  start, so it goes out even when nobody opens start.ng at 6 PM.

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

## Spotlight: Game of the Week and the daily challenge (PRD A2)
`app/features/spotlight`; flag `spotlight` (uses `progress` for runs); admin permission `spotlight.edit`. Weekly jams are Phase 3.
- **Game of the Week:** a pick for a week (Monday to Sunday, Nigeria time), chosen in the admin Spotlight tab: `PUT`
  `/admin/spotlight/game-of-the-week` `{game, note?, week?}` (this week by default, or book a later one),
  `DELETE ...?week=`, `GET /admin/spotlight`. A hidden game is never the pick. Other features read it with
  `spotlight.service.game_of_the_week(db)` (the weekly digest, H1).
- **Daily challenge:** one personal goal a day on one game ("Reach 800 in Relic"), made the first time anyone asks for it that
  day. The game comes from those with at least 10 runs by 3 players in the last 14 days (never the same game two days
  running); the goal is the first leaderboard stat's value that at least 40% of those runs reached, so it is reachable by
  construction. A time game ("lower is better") gets a ceiling. It resets at 00:00 Nigeria time, uses only runs (never the
  daily tournament's entries), ignores flagged runs and runs from before the day, and a player completes it once a day
  (even if they played before looking at it). `GET /spotlight` (both) and `GET /spotlight/challenge`.
- **Badge:** completing it earns `daily_challenger` through the Progress Engine (a D1 rule, once). The profile page's catalog
  (S1) lists only its own badges, so add this one there to show it.
## Discovery: shelves and ranking (PRD A1)
`app/features/discovery`; flag `discovery`; admin permission `featured.edit`. Games come back as names; the website has the games.
- **Ranking** (`game_rank`, contract C10): per game, from the last 7 days: different people who opened it, scores per
  player, and the share of players who came back on another day, times a penalty when the Kit says sessions are short
  (a 10-second average keeps a third of the score; no Kit data, no penalty) and a 1.5x boost for games under a week old.
  The background loop rewrites it every 10 minutes, and a read refreshes it when it is over 30 minutes old. Session length comes
  from `app.features.kit.service.average_session_seconds(db, game, since)` once the Kit (M1) publishes it.
- `GET /discover/shelves`: `featured`, `staff` (chosen in the admin Featured tab), `new` (every game under 7 days old,
  whatever its rank), `most_played` (by rank), `most_improved` (growth against the week before, at least 3 people) and
  `continue_playing` (your own games from game opens; empty when signed out).
- **Featuring:** `eligible_for_featuring(db, game)` needs the game standards, a cover, a description, a passing health
  check (H2), a leaderboard, and `"mobile": true` in startng.json (a new section). Using the Kit is preferred, not
  required. Staff picks have no checklist. Owners see theirs with `GET /games/{name}/featuring`.
  Admins: `GET /admin/featured`, `PUT`/`DELETE /admin/featured/{featured|staff}/{game}`, `POST .../move?direction=up|down`.
- **Play next** (C10): `GET /discover/next?after=<game>&limit=1-3`: best ranked first, games the player hasn't opened ahead
  of those they have.
## Categories, ratings and filters (PRD T1)
`app/features/catalog`; flag `catalog` (ratings also need `progress`, for game opens); admin permission `ratings.moderate`.
- **Categories:** Action, Racing, Puzzle, Card & board, Arcade, Adventure, Sports, Strategy, Educational. A game sets one in
  its startng.json (`"category": "Puzzle"`, any capitals; anything else refuses the publish with the list), or the owner sets it
  with `PUT /games/{name}/category` `{category}` (null clears it). **The file wins**: a publish whose startng.json has a
  category replaces the owner's choice. A game with none is Uncategorized.
- **Ratings:** `PUT /games/{name}/rating` `{stars: 1-5}` (again to change), `DELETE` to take it back,
  `GET /games/{name}/rating` (totals, yours, and whether you can rate). One per player, not for your own game, and only
  after you opened the game at least 60 seconds ago (the Progress Engine's `game_opens`). Stars show from 5 ratings;
  ordering uses a smoothed score (`(3.5*5 + sum of stars) / (5 + count)`).
- **For the website and other features (C12):** `GET /catalog` lists categories and each game's `category`, `rating_count`,
  `rating_avg` and smoothed `score`; in Python `catalog.service.info(db, game)`. Totals live in their own table
  (`game_catalog`), so nothing in `games` changes.
- **Admins:** `GET /admin/ratings?game=`, `DELETE /admin/ratings/{id}` (logged).
## Player profiles and badges (PRD S1)
`app/features/profiles`; flag `profiles` (uses `progress` for level, streaks and badges). A page for every player: name,
character, level and XP, streaks, badges, best score and rank per game (`rank_of`), most played games, favorites, and the
tournament record. **Never an email.**
- **Privacy:** public by default; `PUT /me/profile-settings` `{public, show_favorites, show_ranks, notice_seen}` switches
  parts off. A private page answers 404 to everyone but its owner, and the Progress Engine's `GET /players/{id}/progress`
  follows the same choice (`progress.register_visibility`). `notice_seen` drives the first-time "your page is public" notice.
- **API:** `GET /players/{id}/profile` (public; your own page also returns `settings`), `GET /me/profile`, `GET /badges`.
- **Badges** (`badges.py`): first score, 3-day streak, 7-day streak, Top 10 (held across two runs), tournament winner,
  tried 10 games. They are awarded by rules registered with the Progress Engine (kind `badge`), once, after the run that
  earns them; players who already qualify get theirs on their next run. Add a badge with one `Badge` and one `Rule`.
- The website adds other features' sections to the page (`registerProfileSection`).
## Creator hub (PRD T2)
`app/features/creator_hub`; flag `creator_hub` (the follower count needs `follows`, the card needs `share_cards`). Aggregates only:
players are never identified to creators.
- **Public page** `GET /by/{id}`: name and character, a short bio, their visible games (hidden ones are left out) with plays
  in the last 30 days, total plays, different people who played in 30 days, and followers (S2). Never an email.
  `GET /by/{id}/page` is the website's HTML with the creator's name and card in its tags (nginx asks for it on `/by/<id>-<name>`).
  `PUT /me/creator-bio` `{bio}` (200 characters). A creator is anyone with a game live on start.ng; others are a 404.
- **Top creators** `GET /by`: by different people who opened their games in the last 30 days, with their game counts.
- **Dashboard** `GET /me/creator-dashboard` (signed in): per game (hidden ones marked) plays in 7 and 30 days and all time,
  different people, signed-in players, repeat players (scores on two or more different days), scores sent, the 7-day change,
  a 30-day trend, and average session length. Session length comes from the Kit (M1): the Kit publishes
  `app.features.kit.service.average_session_seconds(db, game, since)`; until then it is `null`.
- **Rollup:** every hour the background loop writes the finished days that aren't rolled up yet into `game_daily_stats`
  (one row per game per day, Nigeria time), so history survives the 30-day deletion of raw plays. Days after the last
  rollup (today) are counted live. A missed night is filled in by the next run.
- **Share card:** kind `creator` (id = the creator's user id), described in `card.py` for B2's `share_cards`.
