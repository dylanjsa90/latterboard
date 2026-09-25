# Latterboard

> FastAPI backend for webcade's browser games: daily puzzles, snake scores and leaderboards,
> head-to-head matches with matchmaking and a computer opponent, and a websocket for live play.

Last updated: 2026-09-22

Python 3.10+ with uv, SQLAlchemy, Redis, JWT auth. SQLite locally; deploys to Render as a Docker
container built from `Dockerfile`. Setup, Docker, and the production server are in `README.md`.

## Commands

| Command                         | Does                                                   |
| ------------------------------- | ------------------------------------------------------ |
| `make check`                    | mypy + pytest, printing only errors and summary lines  |
| `uv run pytest`                 | Tests (~55s, needs the local Redis)                    |
| `uv run ruff check app/ tests/` | Lint (logging instead of `print`: ruff enforces T201)  |
| `uv run mypy app/`              | Type check, strict mode                                |
| `fastapi dev app/main.py`       | Dev server; auto-reloads, so it picks up edits as you work |

ruff and mypy already report many errors on `dev`, so compare only the files you touched. For
demos, export `DB_TYPE=sqlite SQLITE_DATABASE_URL=sqlite:///<scratch file>` so seeding doesn't
touch the real `sql_app.db`.

## Key Context Files

Paths are in backticks, not `@` imports, so they load only when read.

| When working on...                                            | Read first                                        |
| ------------------------------------------------------------- | ------------------------------------------------- |
| Finding a file, what a module exports                         | `docs/architecture/backend.md`                    |
| Matches, race/co-op modes, matchmaking, the computer opponent | `docs/architecture/matches.md`                    |
| Any REST call, websocket frame, or new endpoint               | [Cross-repo contract](#cross-repo-contract) below |
| webcade, the React client                                     | `~/projects/webcade/AGENTS.md`                    |

## Conventions

### Settings and database
- `Settings` (`app/core/config.py`) reads `app/.env.local` while `APP_ENV=local` (the default) and `app/.env` otherwise.
- The database URL is computed from `DB_TYPE`, not read from `DATABASE_URL`: `sqlite` (the default) uses `SQLITE_DATABASE_URL`; any other value builds `postgresql+psycopg2://` from the lowercase `user`, `password`, `host`, `port`, `dbname` env vars. A `DATABASE_URL` env var only fills the unused `RAW_DATABASE_URL`, so the psycopg3 rewrite in `app/database.py` never runs. Both `psycopg[binary]` and `psycopg2-binary` are installed; for driver import errors, check `DB_TYPE` and the URL scheme before touching dependencies.
- Tables come from `Base.metadata.create_all` (`app/init_db.py`). There are no Alembic migrations yet, so new columns and constraints won't reach existing databases. Call this out whenever you change a model.
- The one exception is `ensure_user_columns` (same file): at startup it adds any missing **nullable** `user` column. New `User` fields must therefore be nullable; anything else still needs that call-out.

### Dates and times
- DB `DateTime` columns are **naive UTC** (SQLite returns them naive). Use `app.utils.utcnow()` for anything compared to or stored in them. Comparing against `datetime.now(timezone.utc)` raises `TypeError`.
- Calendar dates are UTC: routes, seeding, and tests. Use `app.game.puzzles.today()`, never `date.today()`, which is local time and runs a day off in US evenings.
- The scores daily cap is the exception, and it is inconsistent: `POST /scores/` and `GET /scores/me/{game}/daily-count` count the US Pacific day, while `GET /scores/me/{game}/plays-today` and the contract below say UTC. Confirm which is intended before changing daily limits.

### Redis
- Always use `settings.REDIS_URL`; never hardcode `localhost`. Docker compose sets `redis://redis:6379`.
- The pub/sub client in `ConnectionManager` needs `socket_timeout=None`. redis-py 8 defaults to 5s, so an idle channel times out, `_listen` exits, and broadcasts stop without any error. The `redis>=7.4.0` pin allows 8.x.
- `ConnectionManager` creates its Redis client in `start()` because `stop()` closes it. Don't share one client across restarts.
- fastapi-cache `@cache` doesn't work on endpoints that depend on `db`/`current_user`. The default key includes their reprs, which change every request, so it never hits and only piles up keys.

### Users
- `username` is the public handle: `[a-z0-9_]{3,20}`, lowercased by `schemas/user.py`. Look it up case-insensitively (`crud_user.get_user_by_username`). Accounts made before handles still have their email there, so never validate `username` on output.
- `birth_year` is private. Only `UserPrivate` (test-token, `POST /users/`, `PATCH /users/me`) carries it; `UserPublic` (returned to any signed-in user) and `PlayerPublic` must not.
- Google sign-in (`app/core/google.py` checks the ID token, `app/api/google_sign_in.py` holds what the two routes share) links accounts through `user_identity` (`provider`, `subject` = Google's `sub`, never the email). It is a table rather than `user` columns so `create_all` builds its unique constraint. `GOOGLE_CLIENT_ID` (the public web client ID, the same as webcade's `VITE_GOOGLE_CLIENT_ID`) turns it on; tests replace `google.verify_google_id_token`.
- Photos live in `user_avatar` (a separate table, so loading a user never loads the bytes). `app/lib/avatar.py` re-encodes every upload to a 256px WebP, which drops EXIF such as phone GPS. `user.avatar_version` versions the public URL, so it can be cached forever.

### Email
- `render_email_template` uses a plain jinja `Template`, which does **not** autoescape. Escape anything a player wrote (usernames, invite messages) with `markupsafe.escape`, as `generate_match_invite_email` does.
- Templates: edit `app/email-templates/src/*.mjml` and keep `build/*.html` in step. There's no MJML toolchain in the repo, so `build/match_invite.html` is hand-written.
- `send_email` asserts `settings.emails_enabled` (SMTP host and from-address set). Check it before sending; invite links report `emailed: false` instead when it's off.

### Puzzles
- Future-dated puzzle ids must return 404. Otherwise the endpoints leak upcoming answers and create rows for arbitrary dates.
- `PuzzleAttempt.puzzle_id` is a string key like `"word-2026-09-11"`, not a foreign key to `Puzzle.id`.
- `app/game/puzzle_seed_data.py`: use `shuffled_words()`, which returns a copy. Don't shuffle or mutate the shared word list; `wordle.py` uses it too.
- Known open issues (not fixed yet):
  - `attempt_count` comes from the client, so anyone can send 6 and get today's word answer.
  - Word guesses aren't checked with `wordle.is_valid_word`.
  - `(user_id, puzzle_id)` has no unique constraint.

### Tests
- `tests/conftest.py` sets env vars **before** any app import, because `Settings()` reads them at import time. Keep new overrides above the imports.
- Each test drops the DB, recreates it, and re-seeds it with `init_db`. `WS_HEARTBEAT_SECONDS=3600` stops heartbeat frames from interleaving with the websocket frames tests assert on.
- Tests use the same Redis as the dev server (default `REDIS_URL`), so test broadcasts and presence keys show up in dev Redis.
- Two-player fixtures `inviter_headers` / `opponent_headers` (users `inviter` / `opponent`) live in `conftest.py`.
- To simulate a stale concurrent write, keep a reference to the row you loaded. The SQLAlchemy identity map holds objects weakly, so an unreferenced row gets garbage-collected and the next query silently reads fresh state, and the test passes without exercising the conflict.

## Project Policies

- Work on `dev`; open PRs into `main`. Never push to `main` directly.
- `.github/workflows/ci.yml` runs lint, typecheck, test, and a Docker build check on PRs into
  `main` and pushes to `dev`. It's a quality gate only — no deploy step.
- Agents commit or push only when asked.
- A change is done when `make check` is clean for the files you touched — plus `npm run check` in webcade when both repos changed.

### Working style
- State your assumptions. When a request has more than one reading, or something is unclear, name it and ask rather than picking silently.
- Choose the simplest design a senior engineer would sign off on; when a simpler approach exists than the one asked for, say so and push back.
- Make surgical changes: touch only what the task needs, and remove the imports, variables, and functions your own change left unused.
- Turn tasks into verifiable goals: reproduce a bug in a failing test before fixing it, cover new validation with tests for invalid inputs, and keep tests green before and after a refactor.

## Documentation Maintenance

1. Put detail in `docs/` (`architecture/`, `decisions/`, `guides/`, `reference/`, `plans/` —
   create a directory only when it gets its first file), not in this file.
2. Add a row to **Key Context Files** for every new doc; remove the row when a doc is deleted.
3. Rewrite docs to reflect current state rather than appending updates, and bump
   `Last updated:`.
4. kebab-case file names; number ADRs (`001-websocket-topics.md`).
5. The cross-repo contract below changes in both repos at once.

## Working efficiently (token hygiene)
- Read code with Read (offset/limit) or Grep -n, never `cat` multiple files in one Bash call.
- Read a file only when you are about to edit it or the module map (`docs/architecture/backend.md`) is insufficient.
- Verify with `npm run check` (webcade) / `make check` (latterboard). Run the targeted test file first; run the full check once at the end, not after every edit.
- Browser verification: prefer read_page, get_page_text, or javascript_tool assertions over screenshots. Never Read screenshot PNGs back from the scratchpad. If a screenshot times out, retry at most once, then use get_page_text.
- For features touching 5+ files, write a short file-by-file plan and confirm it before the first edit.

## Cross-repo contract

webcade (`~/projects/webcade`, React) is latterboard's (`~/projects/latterboard`, FastAPI) only
client. Keep this section identical in both repos' CLAUDE.md and update both when a shape changes.

- REST is under `/api/v1` (webcade: `API_V1_URL`). The websocket is at the app root, `/ws/{topic}`,
  **not** under `/api/v1`.
- Wire JSON is snake_case; webcade converts to camelCase inside each `api.ts`.
- Errors are FastAPI `{detail: string}`; webcade shows `detail` when it's a string.
- Authenticated calls send `Authorization: Bearer <access_token>` via webcade's `authFetch`; a 401
  ends the webcade session app-wide.
- `Grade` = `"correct" | "present" | "absent"`. Sudoku boards are `int[81]`, `0` = empty.

### Auth / users
| Call | Request | Response |
| ---- | ------- | -------- |
| `POST /login/access-token` | form-urlencoded `username` (= email), `password` | `{access_token, token_type}` |
| `POST /login/test-token` | bearer | `UserPrivate`; 401 = dead token |
| `POST /login/google` | `{credential}` (the ID token from Google's sign-in button) | `{status: "signed_in", access_token, token_type}`, or `{status: "needs_profile", email, name?}` when no account has that Google account or its email; 401 bad or expired credential, 403 unverified Google email, 400 inactive, 404 when `GOOGLE_CLIENT_ID` is unset |
| `POST /users/google` | `{credential, username, display_name, birth_year, location?}` | 201 `{access_token, token_type}`; 409 / 422 as `POST /users/`, and 401 / 403 / 404 as `/login/google` |
| `POST /users/` | `{email, username, password, display_name, birth_year, location?}` | 201 `UserPrivate`; 409 if the email or the username is taken (`detail` says which); 422 for a bad handle or anyone under 13 |
| `PATCH /users/me` (bearer) | any of `{display_name, username, birth_year, location}` (`""`/null clears `location`; null leaves the others) | `UserPrivate`; 409 if the username is taken |
| `GET /users/players?usernames=a&usernames=b` (bearer, ≤ 50) | — | `PlayerPublic[]` for the handles that exist |
| `PUT /users/me/avatar` (bearer) | multipart `file` (JPEG/PNG/WebP, ≤ 2 MB) | `UserPrivate`; 413 too big, 422 not an image. Re-encoded to a 256px WebP without EXIF |
| `DELETE /users/me/avatar` (bearer) | — | 204 |
| `GET /users/{id}/avatar` (public) | — | `image/webp`, cacheable forever (the URL is versioned); 404 without a photo |

`UserPublic` = `{id, email, username, display_name?, location?, avatar_url?, is_active, is_bot,
created_at, updated_at}`; `UserPrivate` = `UserPublic` + `birth_year`, sent only to its owner;
`PlayerPublic` = `{username, is_bot, display_name?, location?, avatar_url?, created_at}`. `is_bot`
marks the computer opponent and is always a real boolean on the wire, even though the column is
nullable. `avatar_url` is a path under the API
(`/api/v1/users/{id}/avatar?v=<hash>`) that webcade prefixes with `API_BASE_URL`. Handles are
`[a-z0-9_]{3,20}`, lowercased, and unique regardless of case; accounts made before handles keep their
email as `username` until they choose one. `birth_year` must make the player 13 or older this (UTC) year.

Google sign-in ends with the same `access_token` as a password. The first `/login/google` whose
verified email matches an existing account (any case) links that Google account to it, and the
password keeps working. A new Google player gets no account until `/users/google` sends a valid
profile with the same credential, which Google keeps valid for an hour. Google-only accounts
have a random password, which password recovery can replace.

### Puzzles (`/puzzles`, bearer; `puzzle_id` like `"word-2026-09-11"`, future dates 404)
| Call | Request | Response |
| ---- | ------- | -------- |
| `GET /word` | — | `{puzzle_id, word_length, max_attempts, initial_guess, initial_grade: Grade[], guesses: [{guess, grades}], won, lost, answer?}` |
| `POST /word/hint` | `{puzzle_id}` | `{letter}` |
| `POST /word/guess` | `{puzzle_id, guess (5 chars), attempt_count}` | `{grades, won, lost, answer?}` |
| `GET /sudoku` | — | `{puzzle_id, puzzle: int[81]}` |
| `POST /sudoku/move` | `{puzzle_id, index 0-80, value 1-9, board}` | `{correct, completed}` |
| `POST /sudoku/hint` | `{puzzle_id, index, board}` | `{value, completed}` |
| `GET /memory` | — | `{puzzle_id, size}` |
| `POST /memory/reveal` | `{puzzle_id, index}` | `{symbol}` |
| `GET /cipher` | — | `{puzzle_id, slots, max_attempts, initial_attempt: int[], initial_feedback}` |
| `POST /cipher/attempt` | `{puzzle_id, attempt: int[4]}` | `CipherFeedback {exact, close, won}` |
| `GET /me/stats` | — | `{total_solved, today_solved, streak, best_streak, games: [{game, played, won}], recent: [{game, solved_on, solved_at, result}]}` |
| `GET /me/history?skip&limit` | — | `{items: [{game, puzzle_id, won, attempt_count, completed_at}], total}` |

### Scores (`/scores`)
| Call | Request | Response |
| ---- | ------- | -------- |
| `POST /` (bearer) | `{game, score ≥ 0}` | 201 `{id, game, score, created_at}`; 429 past `MAX_DAILY_PLAYS_PER_GAME` (5 per UTC day); 422 if `SCORE_RULES` rejects the score |
| `GET /me/{game}` (bearer) | — | `[{id, game, score, created_at}]` |
| `GET /me/{game}/plays-today` (bearer) | — | `{used, limit}` (webcade doesn't mirror the cap) |
| `GET /leaderboard/{game}/{all-time\|daily\|monthly}?limit` | — | `[{rank, username, score, achieved_at}]` |

`SCORE_RULES` (`app/schemas/game_score.py`) must match webcade's `src/components/snake/constants.ts`
(snake: multiples of 10, up to 3990).

### Matches (`/matches`, bearer) and matchmaking
`MatchPublic` = `{id, game, status, inviter_username, invitee_username, inviter_is_bot,
invitee_is_bot, current_turn_username?, winner_username?, max_guesses, created_at, started_at?,
completed_at?}`. The bot flags are per side, not "opponent", because `to_public` builds one object
both players receive; the viewer-relative shapes (`/me/active`, `/me/history`, `match_started`) carry
`opponent_is_bot` instead.
`game` ∈ `wordle | word_race | cipher_race | sudoku_coop`; `status` ∈
`pending_invite | in_progress | completed | declined | cancelled | expired`.

| Call | Request | Response (+ frames sent) |
| ---- | ------- | ------------------------ |
| `POST /matches/invite` | `{opponent_username, game = "wordle"}` | 201 `MatchPublic` (+ `invite_received` to invitee); 400 inviting the computer — search for an opponent instead |
| `POST /matches/{id}/accept` | — | `MatchPublic` (+ `match_started` to both) |
| `POST /matches/{id}/decline` | — | `MatchPublic` (+ `invite_declined` to inviter) |
| `POST /matches/{id}/cancel` | — | `MatchPublic` (+ `invite_cancelled` to invitee) |
| `GET /matches/pending` | — | `[{id, game, from_username, created_at, expires_at}]` |
| `GET /matches/me/history?skip&limit` | — | `{items: [{id, game, opponent_username, opponent_is_bot, result, completed_at}], total, record: {won, lost, drawn}}`, completed matches newest first; `result` ∈ `won \| lost \| draw` (`sudoku_coop`: `solved \| failed`); `record` spans every completed competitive match except those against the computer |
| `GET /matches/me/active` | — | `[{id, game, status: pending_invite \| in_progress, opponent_username, opponent_is_bot, role: inviter \| invitee, your_move, created_at, started_at}]`, newest first; `your_move` = your turn (wordle), your side of a race unfinished, always (co-op), or an invite for you to answer |
| `GET /matches/{id}` | — | `MatchDetail` (below); 403/404 if not a participant / missing |
| `POST /matches/{id}/guess` | `{word}` (wordle, your turn only) | `{match_id, turn_number, username, word, result: [{letter, status}], correct, status, current_turn_username, winner_username}` |
| `POST /matches/{id}/word/guess` | `{word}` | `RaceGuessResult {match_id, turn_number, guess, result, correct, status, winner_username, answer}` |
| `POST /matches/{id}/cipher/guess` | `{attempt: int[4]}` | `RaceGuessResult` (`result` = `{exact, close, won}`) |
| `POST /matches/{id}/sudoku/move` | `{index, value}` | `{match_id, index, value, correct, mistakes, status, outcome}`; 409 if rejected |
| `POST /matchmaking/{game}` | — | `{status: "queued"\|"matched", match: MatchPublic?, queue_ttl_seconds}` (+ `match_started` on pairing). After `MATCHMAKING_BOT_WAIT_SECONDS` with no human, pairs the caller with the computer (not for `sudoku_coop`); `match.invitee_is_bot` says so |
| `DELETE /matchmaking/{game}` | — | 204 |
| `GET /ws/{topic}/usernames` | — (public) | `{topic, usernames}` = the available-players list |
| `GET /ws/{topic}/connections` | — (public) | `{topic, connections}` |

A queued player must re-POST `/matchmaking/{game}` within `queue_ttl_seconds` to stay queued
(webcade re-joins every `ttl / 3`). Re-POSTing does not reset how long they have been waiting, so
polling still reaches the computer fallback.

`MatchDetail` = `MatchPublic` + `{target_word?, guesses: MatchGuessResult[] (wordle), race?, sudoku?}`:
- `race` = `{you, opponent, answer?}`, each player `{username, guesses: [{guess?, result: Grade[] |
  {exact, close, won}, correct}], solved, finished}`. The opponent's `guess` and the `answer` are
  null until `status == "completed"`.
- `sudoku` = `{puzzle, board, mistakes, max_mistakes, outcome: "solved"|"failed"|null, solution?}`.

### Invite links (`/invite-links`)
Single-use links a player sends a friend by text (webcade shares it from the device) or email
(latterboard sends it when SMTP is configured). webcade serves them at `/invite/{token}`; they
last `MATCH_INVITE_LINK_EXPIRY_DAYS` (7), and the link's sender becomes the match's inviter.

| Call | Request | Response (+ frames sent) |
| ---- | ------- | ------------------------ |
| `POST /` (bearer) | `{game, channel: email \| text \| link, message? ≤ 280, email?}` (`email` iff `channel == "email"`) | 201 `InviteLink {token, url, game, message, channel, recipient_email, created_at, expires_at, emailed}`; 429 past `MATCH_INVITE_LINK_DAILY_LIMIT` (20 per UTC day) |
| `GET /mine` (bearer) | — | `InviteLink[]` nobody has used, withdrawn, or let expire, newest first (`emailed` is false here) |
| `GET /{token}` (public) | — | `{inviter_username, game, message, expires_at, status: open \| claimed \| expired \| revoked}`; 404 if unknown |
| `POST /{token}/claim` (bearer) | — | `MatchPublic` (+ `match_started` to both), reusing an open match the pair already shares; the same match again for its claimer; 400 own link, 409 used by someone else, 410 expired or withdrawn |
| `DELETE /{token}` (bearer, sender) | — | 204; 409 once used |

### WebSocket `/ws/{topic}?token=<access_token>`
Browsers can't set headers on a websocket, so the token rides in the query string. A socket joins
`{topic}` (webcade: `lobby`, or a game id while "available") plus the per-user topic `user:{id}`.

| Direction | Frame |
| --------- | ----- |
| server → on connect | `connected {topic, username}` |
| client → server | `join_match {match_id}` (participants only; answered `joined_match {match_id}`), `leave_match {match_id}`. Any other text is rebroadcast to the topic as `message {topic, username, data}` |
| `user:{id}` | `invite_received {match_id, from_username, game, created_at, expires_at}`, `invite_declined {match_id}`, `invite_cancelled {match_id}`, `match_started {match_id, game, opponent_username, opponent_is_bot, current_turn_username, max_guesses, …}` |
| `match:{id}` | `opponent_guessed {match_id, username, turn_number, result, correct, word?}` (`word` only in wordle; races send grades only), `your_turn {match_id, current_turn_username}`, `cell_filled {match_id, username, index, value}`, `mistake {match_id, username, index, value, mistakes, max_mistakes}`, `match_completed {match_id, …}` (wordle: `winner_username, target_word, reason`; races: `winner_username, answer, reason`; co-op: `outcome`) |
| everyone | `heartbeat` every `WS_HEARTBEAT_SECONDS` |

webcade treats every `match:{id}` frame as "reload `GET /matches/{id}`". Fields beyond
`type`/`match_id` are informational, so adding fields is safe but renaming a `type` is not.
