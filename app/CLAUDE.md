# CLAUDE.md


## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

---

# Project notes (Latterboard API)

Commands: `make check` (mypy + pytest, errors/summary only), `uv run pytest`, `uv run ruff check app/ tests/`, `uv run mypy app/`. Dev server: `fastapi dev app/main.py` (auto-reloads, so it picks up edits while you work).

## Dates and times
- DB `DateTime` columns are **naive UTC** (SQLite returns them naive). Use `app.utils.utcnow()` for anything compared to or stored in them. Comparing against `datetime.now(timezone.utc)` raises `TypeError`.
- Calendar dates are UTC everywhere: routes, seeding, and tests. Use `app.game.puzzles.today()`, never `date.today()`, which is local time and runs a day off in US evenings.

## Redis
- Always use `settings.REDIS_URL`; never hardcode `localhost`. Docker compose sets `redis://redis:6379`.
- The pub/sub client in `ConnectionManager` needs `socket_timeout=None`. redis-py 8 defaults to 5s, so an idle channel times out, `_listen` exits, and broadcasts stop without any error. The `redis>=7.4.0` pin allows 8.x.
- `ConnectionManager` creates its Redis client in `start()` because `stop()` closes it. Don't share one client across restarts.
- fastapi-cache `@cache` doesn't work on endpoints that depend on `db`/`current_user`. The default key includes their reprs, which change every request, so it never hits and only piles up keys.

## Database / schema
- Tables come from `Base.metadata.create_all` (`app/init_db.py`). There are no Alembic migrations yet, so new columns and constraints won't reach existing databases. Call this out whenever you change a model.
- The one exception is `ensure_user_columns` (same file): at startup it adds any missing **nullable** `user` column. New `User` fields must therefore be nullable; anything else still needs that call-out.

## Users
- `username` is the public handle: `[a-z0-9_]{3,20}`, lowercased by `schemas/user.py`. Look it up case-insensitively (`crud_user.get_user_by_username`). Accounts made before handles still have their email there, so never validate `username` on output.
- `birth_year` is private. Only `UserPrivate` (test-token, `POST /users/`, `PATCH /users/me`) carries it; `UserPublic` (returned to any signed-in user) and `PlayerPublic` must not.
- Photos live in `user_avatar` (a separate table, so loading a user never loads the bytes). `app/lib/avatar.py` re-encodes every upload to a 256px WebP, which drops EXIF such as phone GPS. `user.avatar_version` versions the public URL, so it can be cached forever.
- `PuzzleAttempt.puzzle_id` is a string key like `"word-2026-09-11"`, not a foreign key to `Puzzle.id`.
- Race and co-op matches keep their private puzzle and live state in `match_puzzle`, not on `match`. It's a new table so `create_all` can add it to existing databases without a migration. Their `match.target_word` is `""` (the column is non-null).

## Matches
- `match.game` is one of: `wordle` (turn-based, shared board, the original mode), `word_race` / `cipher_race` (same private puzzle, played at the same time on separate boards, fewest attempts wins), or `sudoku_coop` (one shared board, shared mistake limit). The rules live in `app/game/match_modes.py`.
- Every move endpoint must call `_require_game`. Without it, a race match sent to `/guess` runs the turn-based logic against a `target_word` of `""`.
- `match.max_guesses` holds attempts per player in races, and the team's mistake limit in `sudoku_coop`.
- In races, never send a player's guess (letters/digits) to the opponent while the match is in progress. Send only grades, both in websocket frames and in `GET /matches/{id}`. The answer and guesses are revealed once `status == "completed"`.
- Change `match_puzzle` state only through `CRUDMatchPuzzle._apply`, and have the change function re-check `match.status` itself. `version` is an optimistic lock: when both players move at once, the losing write is replayed on fresh state instead of overwriting the other move.
- Match puzzles are freshly generated per match, not the daily `puzzle` rows. For sudoku use `generate_sudoku()`; `sudoku_variant()` only yields 9 distinct boards.
- Start a match between two known players only through `app/api/match_start.py`: `start_match` (reuses an open match the pair already shares, else creates and accepts one) and `notify_match_started` (the `match_started` frame to both). `/accept`, matchmaking, and invite-link claims all use it.
- Invite links (`match_invite_link`, `app/api/routes/invite_links.py`) are single-use and name no invitee until claimed; claiming makes the link's sender the match's inviter. Claims use a conditional UPDATE so two people can't both win one link.
- Matchmaking (`app/api/routes/matchmaking.py`) keeps one Redis sorted set per game (`mm:queue:{game}`), scored by each player's last join. `POST /matchmaking/{game}` atomically (Lua) pairs the caller with the longest-waiting other player or queues them; a pairing creates and accepts the match at once and sends the same `match_started` frame as `/accept`. Entries older than `MATCHMAKING_QUEUE_TTL_SECONDS` are skipped, so clients must re-POST to stay queued. That's how a closed tab leaves the queue. Its tests clear `mm:queue:*` themselves, since users are recreated with the same ids.

## Email
- `render_email_template` uses a plain jinja `Template`, which does **not** autoescape. Escape anything a player wrote (usernames, invite messages) with `markupsafe.escape`, as `generate_match_invite_email` does.
- Templates: edit `app/email-templates/src/*.mjml` and keep `build/*.html` in step. There's no MJML toolchain in the repo, so `build/match_invite.html` is hand-written.
- `send_email` asserts `settings.emails_enabled` (SMTP host and from-address set). Check it before sending; invite links report `emailed: false` instead when it's off.

## Puzzles
- Future-dated puzzle ids must return 404. Otherwise the endpoints leak upcoming answers and create rows for arbitrary dates.
- `app/game/puzzle_seed_data.py`: use `shuffled_words()`, which returns a copy. Don't shuffle or mutate the shared word list; `wordle.py` uses it too.
- Known open issues (not fixed yet):
  - `attempt_count` comes from the client, so anyone can send 6 and get today's word answer.
  - Word guesses aren't checked with `wordle.is_valid_word`.
  - `(user_id, puzzle_id)` has no unique constraint.

## Tests
- `tests/conftest.py` sets env vars **before** any app import, because `Settings()` reads them at import time. Keep new overrides above the imports.
- Each test drops the DB, recreates it, and re-seeds it with `init_db`. `WS_HEARTBEAT_SECONDS=3600` stops heartbeat frames from interleaving with the websocket frames tests assert on.
- Tests use the same Redis as the dev server (default `REDIS_URL`), so test broadcasts and presence keys show up in dev Redis.
- Two-player fixtures `inviter_headers` / `opponent_headers` (users `inviter` / `opponent`) live in `conftest.py`.
- To simulate a stale concurrent write, keep a reference to the row you loaded. The SQLAlchemy identity map holds objects weakly, so an unreferenced row gets garbage-collected and the next query silently reads fresh state, and the test passes without exercising the conflict.

## Working efficiently (token hygiene)
- Read code with Read (offset/limit) or Grep -n, never `cat` multiple files in one Bash call.
- Read a file only when you are about to edit it or the architecture map below is insufficient.
- Verify with `npm run check` (webcade) / `make check` (latterboard). Run the targeted test file first; run the full check once at the end, not after every edit.
- Browser verification: prefer read_page, get_page_text, or javascript_tool assertions over screenshots. Never Read screenshot PNGs back from the scratchpad. If a screenshot times out, retry at most once, then use get_page_text.
- For features touching 5+ files, write a short file-by-file plan and confirm it before the first edit.

## Architecture map

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `app/main.py` | FastAPI app: lifespan (Redis, fastapi-cache, `manager.start/stop`), `/health`, mounts `api_router` at `/api/v1`, and **the websocket `/ws/{game_name}`** (join/leave_match handling) | `app` |
| `app/api/main.py` | Router aggregation: login, users, scores, matches, matchmaking, puzzles, ws | `api_router` |
| `app/api/deps.py` | Request dependencies | `get_db`, `get_current_user`, `get_optional_current_user`, `get_current_user_ws` (token query param), `get_current_active_superuser` |
| `app/api/routes/matches.py` | `/matches/*`: invite/accept/decline/cancel/pending/detail, `/me/history`, `/me/active` + one move endpoint per mode; each move calls `_require_game`, then broadcasts to `match:{id}` | `router`, `SUPPORTED_GAMES` |
| `app/api/routes/matchmaking.py` | `POST`/`DELETE /matchmaking/{game}`: Redis ZSET queue + Lua pairing | `router` |
| `app/api/match_start.py` | Starting a match between two known players, shared by accept, matchmaking, and invite links | `start_match`, `notify_match_started`, `max_guesses_for` |
| `app/api/routes/invite_links.py` | `/invite-links/*`: create (emails via `BackgroundTasks` when SMTP is on), list mine, public preview, claim, revoke | `router`, `GAME_TITLES` |
| `app/crud/invite_link.py` | Invite link rows: create, daily count, open links, status, atomic claim, revoke | `invite_link`, `invite_url` |
| `app/schemas/invite_link.py` | Invite link I/O models | `InviteLinkCreate`, `InviteLinkPublic`, `InviteLinkPreview`, `INVITE_MESSAGE_MAX` |
| `app/api/routes/users.py` | `/users/*`: sign-up, `PATCH /me`, `/players` lookup, photo upload/remove/serve, admin-style list/get/update/delete. Literal paths sit above `/{user_id}` | `router` |
| `app/schemas/user.py` | User I/O + the handle/name/city/birth-year rules | `UserCreate`, `ProfileUpdate`, `UserPublic`, `UserPrivate`, `PlayerPublic`, `avatar_url` |
| `app/lib/avatar.py` | Validates and re-encodes profile photos | `process_avatar`, `InvalidImage`, `AVATAR_MAX_BYTES` |
| `app/init_db.py` | `create_all`, `ensure_user_columns`, the seed user and puzzles | `init_db`, `ensure_user_columns` |
| `app/api/routes/ws.py` | REST presence only (`/ws/{game}/connections`, `/ws/{game}/usernames`); the socket itself is in `app/main.py` | `router` |
| `app/api/routes/puzzles.py` | Daily word/sudoku/memory/cipher puzzles + `/me/stats`, `/me/history` | `router` |
| `app/connection_manager.py` | Topic membership, Redis presence, pub/sub delivery, heartbeat | `manager` (`connect`, `join`, `disconnect`, `disconnect_all`, `broadcast(msg, topic)`, `send_to_user(user_id, msg)`, `count`, `usernames`) |
| `app/crud/match.py` | Match lifecycle + turn-based wordle | `match` (`create_invite`, `expire_if_needed`, `get_open_match_between`, `get_pending_invites`, `get_active`, `get_active_items`, `accept_invite`, `decline_invite`, `cancel_invite`, `submit_guess`, `to_public`, `to_detail`, `to_pending_invite`) |
| `app/crud/match_puzzle.py` | Race/co-op state in `match_puzzle`, optimistic lock via `_apply` | `match_puzzle` (`build`, `submit_race_guess`, `race_detail`, `submit_sudoku_move`, `sudoku_detail`), `MoveRejected` |
| `app/crud/puzzle_attempt.py` | Daily puzzle attempts, stats, streaks | `puzzle_attempt` (`get_for_puzzle`, `get_or_create`, `record_attempt`, `get_stats`, `get_history`) |
| `app/schemas/match.py` | Match I/O models | `MatchInviteCreate`, `MatchPublic`, `MatchDetail`, `MatchGuessResult`, `RaceDetail`, `RacePlayer`, `RaceGuessOut`, `RaceGuessResult`, `SudokuCoopMoveCreate`, `SudokuCoopMoveResult`, `SudokuCoopDetail`, `PendingInvite` |
| `app/schemas/puzzle.py` | Puzzle I/O models | `{Word,Sudoku,Memory,Cipher}PuzzlePublic`, `WordGuessCreate`/`WordGuessResult`, `SudokuMoveCreate`/`SudokuMoveResult`, `CipherAttemptCreate`/`CipherFeedback`, `PuzzleStats`, `PuzzleHistory` |
| `app/core/config.py` | Env-driven settings | `settings` (`API_V1_STR`, `REDIS_URL`, `MATCH_MAX_GUESSES`, `MATCH_INVITE_EXPIRY_MINUTES`, `MATCH_INVITE_LINK_EXPIRY_DAYS`, `MATCH_INVITE_LINK_DAILY_LIMIT`, `MATCHMAKING_QUEUE_TTL_SECONDS`, `FRONTEND_HOST`, SMTP settings, …) |
| `tests/conftest.py` | Env overrides before imports, per-test DB reset | fixtures incl. `inviter_headers`, `opponent_headers` |

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
| `POST /users/` | `{email, username, password, display_name, birth_year, location?}` | 201 `UserPrivate`; 409 if the email or the username is taken (`detail` says which); 422 for a bad handle or anyone under 13 |
| `PATCH /users/me` (bearer) | any of `{display_name, username, birth_year, location}` (`""`/null clears `location`; null leaves the others) | `UserPrivate`; 409 if the username is taken |
| `GET /users/players?usernames=a&usernames=b` (bearer, ≤ 50) | — | `PlayerPublic[]` for the handles that exist |
| `PUT /users/me/avatar` (bearer) | multipart `file` (JPEG/PNG/WebP, ≤ 2 MB) | `UserPrivate`; 413 too big, 422 not an image. Re-encoded to a 256px WebP without EXIF |
| `DELETE /users/me/avatar` (bearer) | — | 204 |
| `GET /users/{id}/avatar` (public) | — | `image/webp`, cacheable forever (the URL is versioned); 404 without a photo |

`UserPublic` = `{id, email, username, display_name?, location?, avatar_url?, is_active, created_at,
updated_at}`; `UserPrivate` = `UserPublic` + `birth_year`, sent only to its owner; `PlayerPublic` =
`{username, display_name?, location?, avatar_url?, created_at}`. `avatar_url` is a path under the API
(`/api/v1/users/{id}/avatar?v=<hash>`) that webcade prefixes with `API_BASE_URL`. Handles are
`[a-z0-9_]{3,20}`, lowercased, and unique regardless of case; accounts made before handles keep their
email as `username` until they choose one. `birth_year` must make the player 13 or older this (UTC) year.

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
`MatchPublic` = `{id, game, status, inviter_username, invitee_username, current_turn_username?,
winner_username?, max_guesses, created_at, started_at?, completed_at?}`.
`game` ∈ `wordle | word_race | cipher_race | sudoku_coop`; `status` ∈
`pending_invite | in_progress | completed | declined | cancelled | expired`.

| Call | Request | Response (+ frames sent) |
| ---- | ------- | ------------------------ |
| `POST /matches/invite` | `{opponent_username, game = "wordle"}` | 201 `MatchPublic` (+ `invite_received` to invitee) |
| `POST /matches/{id}/accept` | — | `MatchPublic` (+ `match_started` to both) |
| `POST /matches/{id}/decline` | — | `MatchPublic` (+ `invite_declined` to inviter) |
| `POST /matches/{id}/cancel` | — | `MatchPublic` (+ `invite_cancelled` to invitee) |
| `GET /matches/pending` | — | `[{id, game, from_username, created_at, expires_at}]` |
| `GET /matches/me/history?skip&limit` | — | `{items: [{id, game, opponent_username, result, completed_at}], total, record: {won, lost, drawn}}`, completed matches newest first; `result` ∈ `won \| lost \| draw` (`sudoku_coop`: `solved \| failed`); `record` spans every completed competitive match |
| `GET /matches/me/active` | — | `[{id, game, status: pending_invite \| in_progress, opponent_username, role: inviter \| invitee, your_move, created_at, started_at}]`, newest first; `your_move` = your turn (wordle), your side of a race unfinished, always (co-op), or an invite for you to answer |
| `GET /matches/{id}` | — | `MatchDetail` (below); 403/404 if not a participant / missing |
| `POST /matches/{id}/guess` | `{word}` (wordle, your turn only) | `{match_id, turn_number, username, word, result: [{letter, status}], correct, status, current_turn_username, winner_username}` |
| `POST /matches/{id}/word/guess` | `{word}` | `RaceGuessResult {match_id, turn_number, guess, result, correct, status, winner_username, answer}` |
| `POST /matches/{id}/cipher/guess` | `{attempt: int[4]}` | `RaceGuessResult` (`result` = `{exact, close, won}`) |
| `POST /matches/{id}/sudoku/move` | `{index, value}` | `{match_id, index, value, correct, mistakes, status, outcome}`; 409 if rejected |
| `POST /matchmaking/{game}` | — | `{status: "queued"\|"matched", match: MatchPublic?, queue_ttl_seconds}` (+ `match_started` to both on pairing) |
| `DELETE /matchmaking/{game}` | — | 204 |
| `GET /ws/{topic}/usernames` | — (public) | `{topic, usernames}` = the available-players list |
| `GET /ws/{topic}/connections` | — (public) | `{topic, connections}` |

A queued player must re-POST `/matchmaking/{game}` within `queue_ttl_seconds` to stay queued
(webcade re-joins every `ttl / 3`).

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
| `user:{id}` | `invite_received {match_id, from_username, game, created_at, expires_at}`, `invite_declined {match_id}`, `invite_cancelled {match_id}`, `match_started {match_id, game, opponent_username, current_turn_username, max_guesses, …}` |
| `match:{id}` | `opponent_guessed {match_id, username, turn_number, result, correct, word?}` (`word` only in wordle; races send grades only), `your_turn {match_id, current_turn_username}`, `cell_filled {match_id, username, index, value}`, `mistake {match_id, username, index, value, mistakes, max_mistakes}`, `match_completed {match_id, …}` (wordle: `winner_username, target_word, reason`; races: `winner_username, answer, reason`; co-op: `outcome`) |
| everyone | `heartbeat` every `WS_HEARTBEAT_SECONDS` |

webcade treats every `match:{id}` frame as "reload `GET /matches/{id}`". Fields beyond
`type`/`match_id` are informational, so adding fields is safe but renaming a `type` is not.
