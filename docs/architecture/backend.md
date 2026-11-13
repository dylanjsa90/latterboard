# Backend module map

Last updated: 2026-11-12

Where things live in `app/` and what each module exports. Rules for matches, matchmaking, and
the computer opponent are in `docs/architecture/matches.md`; wire shapes are in the cross-repo
contract, `docs/reference/cross-repo-contract.md`; migrations are in
`docs/guides/database-migrations.md`.

## Layers

Calls flow `api/` → `services/` → `crud/` → `game/`, never back up: `crud/` imports neither
`services/` nor `api/`, and `services/` doesn't import `api/`.

- `api/`: HTTP and the websocket. Routes, request dependencies, errors turned into status codes.
- `services/`: work shared by several routes, or by a route and the bot. Starting and playing
  matches, driving the bot, Google sign-in, photo processing.
- `crud/`: reading and writing rows. One module-level instance per model (`app.crud.user.user`,
  imported as `crud_user`).
- `game/`: rules with no database or I/O. Grading, puzzles, bot move choice, Pacific days.
- `models/`, `schemas/`, `core/`: tables, wire models, settings and infrastructure, imported from
  every layer.

Packages don't re-export from `__init__` (except `app.models`, whose import also registers every
table), so imports always name the module.

## Entry points

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `app/main.py` | FastAPI app: lifespan (`init_db`, `manager.start/stop`), middleware, `/health`; mounts `api_router` at `/api/v1` and `ws.socket_router` at the root | `app` |
| `app/backend_pre_start.py` | Waits for the database before the container migrates and serves (`python -m app.backend_pre_start`) | `init`, `main` |
| `app/init_db.py` | Seed rows at startup: the local default user, the bot, puzzles. Never creates tables | `init_db`, `seed_bot_user` |
| `app/schema_drift.py` | Read-only check that a database's schema matches the models (`python -m app.schema_drift`) | `schema_diff`, `describe` |
| `app/init_logger.py` | Loguru to stdout at `LOG_LEVEL`, with stdlib logging (Uvicorn included) routed through it and `token=` query values redacted | `setup_loguru` |
| `alembic/` | Migrations (`versions/`); `env.py` takes the URL from `Settings` | |

## `app/api/`

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `api/main.py` | Router aggregation: login, users, scores, matches, matchmaking, invite_links, puzzles, vocab, ws | `api_router` |
| `api/deps.py` | Request dependencies | `get_db`, `get_current_user`, `get_optional_current_user`, `get_current_user_ws` (token query param), `get_current_active_superuser` |
| `api/routes/login.py` | `/login/*`: password token, `/login/google`, test-token; password recovery, reset, and the superuser email preview | `router` |
| `api/routes/users.py` | `/users/*`: sign-up (password, and `/google` after `needs_profile`), `PATCH /me`, `/players` lookup, photo upload/remove/serve, admin list/get/update/delete. Literal paths sit above `/{user_id}` | `router` |
| `api/routes/scores.py` | `/scores/*`: submit (daily cap), my scores, plays-today, leaderboards | `router` |
| `api/routes/puzzles.py` | Daily word/sudoku/memory/cipher puzzles + `/me/stats`, `/me/history` | `router` |
| `api/routes/matches.py` | `/matches/*`: invite/accept/decline/cancel/pending/detail, `/me/history`, `/me/active` + one move endpoint per mode; each move calls `_require_game`, then broadcasts to `match:{id}` | `router`, `SUPPORTED_GAMES` |
| `api/routes/matchmaking.py` | `POST`/`DELETE /matchmaking/{game}`: Redis ZSET queue + Lua pairing, the bot after `MATCHMAKING_BOT_WAIT_SECONDS` | `router`, `queue_key`, `waiting_since_key` |
| `api/routes/invite_links.py` | `/invite-links/*`: create (emails via `BackgroundTasks` when SMTP is on), list mine, public preview, claim, revoke | `router`, `GAME_TITLES` |
| `api/routes/vocab.py` | `/vocab/*`: Vocab Challenger rooms (create, poll, join, actions) and the wordbook. Rooms aren't matches; a change sends `room_changed` on `vocab:{CODE}` | `router` |
| `api/routes/ws.py` | **The websocket `/ws/{game_name}`** (join/leave_match, join/leave_room for vocab rooms) and REST presence (`/ws/{game}/connections`, `/ws/{game}/usernames`) | `socket_router` (at the root), `router` (under `/api/v1`) |

## `app/services/`

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `services/match_start.py` | Starting a match between two known players, shared by accept, matchmaking, and invite links | `start_match`, `notify_match_started`, `max_guesses_for` |
| `services/match_play.py` | Applying a move and broadcasting it, shared by the move routes and the bot. Raises `MoveRejected`, never `HTTPException` | `apply_wordle_guess`, `apply_race_guess`, `wordle_move_error` |
| `services/bot_turn.py` | When the computer moves and driving it, via `BackgroundTasks` | `schedule_bot_turn`, `run_bot_turn`, `bot_participant`, `BOT_GAMES` |
| `services/google_sign_in.py` | What `/login/google` and `POST /users/google` share: verify the credential (mapping errors to 401/403/404), mint the token | `PROVIDER`, `verified_identity`, `access_token` |
| `services/avatar.py` | Validates and re-encodes profile photos to a 256px WebP (dropping EXIF) | `process_avatar`, `InvalidImage`, `AVATAR_MAX_BYTES` |

## `app/crud/`

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `crud/base.py` | Generic get/create/update/remove | `CRUDBase` |
| `crud/user.py` | Users, identities (Google links), handles, avatars, and the one answer to "is this a bot?" | `user` (`get_user_by_username`, `get_user_by_email_any_case`, `get_user_by_identity`, `link_identity`, `create_user_with_identity`, `get_players`, `bot_ids`, `any_are_bots`, `set_avatar`, `authenticate`, …) |
| `crud/game_score.py` | Score rows, daily play count, all-time/daily/monthly leaderboards | `game_score` |
| `crud/puzzle.py` | The dated puzzle bank: seeding, lookup by game and date, generating a sudoku once the bank runs out | `puzzle` (`seed_defaults`, `get_by_game_date`, `get_or_generate`, `count_by_game`) |
| `crud/puzzle_attempt.py` | Daily puzzle attempts, stats, streaks | `puzzle_attempt` (`get_for_puzzle`, `get_or_create`, `record_attempt`, `get_stats`, `get_history`) |
| `crud/match.py` | Match lifecycle + turn-based wordle, and the match presenters | `match` (`create_invite`, `expire_if_needed`, `get_open_match_between`, `get_pending_invites`, `get_active`, `get_active_items`, `accept_invite`, `decline_invite`, `cancel_invite`, `submit_guess`, `to_public`, `to_detail`, `to_pending_invite`) |
| `crud/match_puzzle.py` | Race/co-op state in `match_puzzle`, optimistic lock via `_apply` | `match_puzzle` (`build`, `submit_race_guess`, `race_detail`, `submit_sudoku_move`, `sudoku_detail`) |
| `crud/invite_link.py` | Invite link rows: create, daily count, open links, status, atomic claim, revoke | `invite_link`, `invite_url` |
| `crud/vocab.py` | Loads a room, runs a rule, saves it with an optimistic lock (`VocabRoom.version`, replayed on `StaleDataError`); `create_room` also deletes rooms a week past expiry; wordbook rows | `crud_vocab` (`create_room`, `mutate`, `wordbook`, `save`, `remove`, `review`); not `vocab`, which the module uses for the game rules |

## `app/game/` (no database or I/O)

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `game/puzzles.py` | Daily puzzle logic, and the Pacific day every daily feature uses: `today()`, `day_start` (a day's naive-UTC start, for `created_at` ranges), `day_of` (a timestamp's day), puzzle-id dates, word grading, cipher feedback and stored codes, memory decks | `PACIFIC`, `today`, `day_start`, `day_of`, `dated_puzzle`, `puzzle_date`, `grade_word`, `cipher_feedback`, `encode_cipher`, `decode_cipher`, `seeded_memory_deck` |
| `game/puzzle_seed_data.py` | Content and generators for the daily puzzles: word order, sudoku variants and generation, cipher digits, memory symbols | `shuffled_words` (a copy; never mutate `word_list`), `SUDOKU_VARIANTS`, `generate_sudoku`, `generate_cipher_digits`, `MEMORY_SYMBOLS` |
| `game/wordle.py` | Wordle word selection, validation, and guess grading | `select_word`, `is_valid_word`, `evaluate_guess` |
| `game/word_list.py` | The 2,309 five-letter answer words, shared by puzzles, matches, and the bot | `word_list` |
| `game/match_modes.py` | Rules for the puzzle match modes (`word_race`, `cipher_race`, `sudoku_coop`): new puzzles, grading, outcomes, scores | `RACE_GAMES`, `PUZZLE_MATCH_GAMES`, `new_puzzle`, `initial_state`, `grade_race_guess`, `race_outcome`, `race_score`, `sudoku_coop_score` |
| `game/bot.py` | Move selection for the computer: filter to still-possible answers, rank, then pick, handicapped by `skill` | `choose_word`, `choose_cipher`, `word_candidates`, `cipher_candidates`, `DEFAULT_SKILL` |
| `game/vocab.py` | Vocab Challenger rules, ported from vocab-app's `engine.ts`: rounds, deadlines (epoch ms), scoring, per-player views, review ladder | `make_game`, `join`, `touch`, `apply_action`, `visible`, `full_question`, `next_review`, `VocabError` |
| `game/vocab_words.py` | The 46-word curriculum; a word's id is its index, so append only | `WORDS`, `VocabWord`, `daily_word` |
| `game/errors.py` | The error a refused move raises, shared by crud, services and routes (routes map it to 409) | `MoveRejected` |

## `app/models/` and `app/schemas/`

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `models/user.py`, `user_identity.py`, `user_avatar.py` | Users; external sign-in links (`provider`, `subject`, unique together); photos, kept apart so loading a user never loads the bytes | `User`, `UserIdentity`, `UserAvatar` |
| `models/game_score.py`, `puzzle.py` | Scores; the dated puzzle bank and players' attempts (`puzzle_id` is a string key, not a foreign key) | `GameScore`, `Puzzle`, `PuzzleAttempt` |
| `models/match.py` | Matches, turn-based guesses, race/co-op puzzle state, invite links | `Match`, `MatchGuess`, `MatchPuzzle`, `MatchInviteLink` |
| `models/vocab.py` | Vocab rooms (one JSON document each) and wordbook rows | `VocabRoom`, `VocabSavedWord` |
| `schemas/user.py` | User I/O + the handle/name/city/birth-year rules | `UserCreate`, `ProfileUpdate`, `UserPublic`, `UserPrivate`, `PlayerPublic`, `Token`, `NewPassword`, `avatar_url` |
| `schemas/game_score.py` | Score I/O models + per-game validity rules | `SCORE_RULES`, `GameScoreCreate`, `LeaderboardEntry`, `PlaysToday` |
| `schemas/puzzle.py` | Puzzle I/O models | `{Word,Sudoku,Memory,Cipher}PuzzlePublic`, `WordGuessCreate`/`WordGuessResult`, `SudokuMoveCreate`/`SudokuMoveResult`, `CipherAttemptCreate`/`CipherFeedback`, `PuzzleStats`, `PuzzleHistory` |
| `schemas/match.py` | Match I/O models | `MatchInviteCreate`, `MatchPublic`, `MatchDetail`, `MatchGuessResult`, `RaceDetail`, `RaceGuessResult`, `SudokuCoopMoveCreate`, `SudokuCoopDetail`, `PendingInvite` |
| `schemas/invite_link.py`, `matchmaking.py`, `vocab.py`, `ws.py` | Invite links; the matchmaking answer; Vocab Challenger (times in epoch ms); presence | `InviteLinkCreate`, `InviteLinkPublic`, `InviteLinkPreview`, `MatchmakingStatus`, `RoomSnapshot`, `Wordbook`, `TopicPresence` |

## `app/core/` and infrastructure

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `core/config.py` | Env-driven settings (`app/.env.local` or `app/.env`, unless `SKIP_ENV_FILE`) | `settings` (`APP_ENV`, `DATABASE_URL`, `REDIS_URL`, `MATCH_*`, `MATCHMAKING_*`, `BOT_*`, `FRONTEND_HOST`, SMTP settings, …) |
| `core/security.py` | Password hashing (argon2), access tokens, password-reset tokens | `get_password_hash`, `verify_password`, `create_access_token`, `generate_password_reset_token`, `verify_password_reset_token` |
| `core/google.py` | Checks a Google ID token against Google's certs and `GOOGLE_CLIENT_ID`; tests replace `verify_google_id_token` | `verify_google_id_token`, `GoogleIdentity`, `InvalidGoogleToken` |
| `core/email.py` | Email rendering (`app/email-templates/build/`, not autoescaped) and SMTP sending | `send_email`, `render_email_template`, `generate_reset_password_email`, `generate_match_invite_email` |
| `core/middleware.py` | Security headers (CSP etc.) on every response | `SecurityHeadersMiddleware` |
| `core/loop_lag.py` | Logs a warning when the event loop stalls by more than 100 ms; started in the lifespan | `watch_loop_lag` |
| `core/redis_client.py` | Shared async Redis client for one-off commands (the `ConnectionManager` keeps its own for pub/sub) | `redis_client` |
| `core/base_class.py` | Declarative base; a model without `__tablename__` gets its lowercased class name | `Base` |
| `app/database.py` | Engine and session factory from `settings.DATABASE_URL` | `engine`, `SessionLocal` |
| `app/connection_manager.py` | Topic membership, Redis presence, pub/sub delivery, heartbeat | `manager` (`connect`, `join`, `disconnect`, `disconnect_all`, `broadcast(msg, topic)`, `send_to_user(user_id, msg)`, their `*_from_thread` twins for sync code in a worker thread, `count`, `usernames`) |
| `app/utils.py` | `utcnow()` for the naive-UTC columns | `utcnow` |

## `tests/`

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `tests/conftest.py` | Env overrides before imports, per-test DB reset, shared fixtures | `client`, `auth_headers`, `superuser_headers`, `inviter_headers`, `opponent_headers`, `empty_queues` |
| `tests/helpers.py` | Plain helpers test files import | `db_session`, `login`, `signup_and_login`, `start_match`, `match_detail` |
| `tests/test_migrations.py` | Migrations build exactly the models; single head | |
