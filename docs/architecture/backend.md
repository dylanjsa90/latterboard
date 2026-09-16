# Backend module map

Last updated: 2026-09-16

Where things live in `app/` and what each module exports. Rules for matches, matchmaking, and
the computer opponent are in `docs/architecture/matches.md`; wire shapes are in the cross-repo
contract in `AGENTS.md`.

| File | Purpose | Key exports |
| ---- | ------- | ----------- |
| `app/main.py` | FastAPI app: lifespan (Redis, fastapi-cache, `manager.start/stop`), `/health`, mounts `api_router` at `/api/v1`, and **the websocket `/ws/{game_name}`** (join/leave_match handling) | `app` |
| `app/api/main.py` | Router aggregation: login, users, scores, matches, matchmaking, puzzles, ws | `api_router` |
| `app/api/deps.py` | Request dependencies | `get_db`, `get_current_user`, `get_optional_current_user`, `get_current_user_ws` (token query param), `get_current_active_superuser` |
| `app/api/routes/matches.py` | `/matches/*`: invite/accept/decline/cancel/pending/detail, `/me/history`, `/me/active` + one move endpoint per mode; each move calls `_require_game`, then broadcasts to `match:{id}` | `router`, `SUPPORTED_GAMES` |
| `app/api/routes/matchmaking.py` | `POST`/`DELETE /matchmaking/{game}`: Redis ZSET queue + Lua pairing | `router` |
| `app/api/match_start.py` | Starting a match between two known players, shared by accept, matchmaking, and invite links | `start_match`, `notify_match_started`, `max_guesses_for` |
| `app/api/match_play.py` | Applying a move and broadcasting it, shared by the move routes and the bot. Raises `MoveRejected`, never `HTTPException` | `apply_wordle_guess`, `apply_race_guess`, `wordle_move_error` |
| `app/api/bot_turn.py` | When the computer moves and driving it, via `BackgroundTasks` | `schedule_bot_turn`, `run_bot_turn`, `bot_participant`, `BOT_GAMES` |
| `app/game/bot.py` | **Pure** move selection for the computer: filter to still-possible answers, rank, then pick, handicapped by `skill` | `choose_word`, `choose_cipher`, `word_candidates`, `cipher_candidates`, `DEFAULT_SKILL` |
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
| `app/core/config.py` | Env-driven settings | `settings` (`API_V1_STR`, `REDIS_URL`, `MATCH_MAX_GUESSES`, `MATCH_INVITE_EXPIRY_MINUTES`, `MATCH_INVITE_LINK_EXPIRY_DAYS`, `MATCH_INVITE_LINK_DAILY_LIMIT`, `MATCHMAKING_QUEUE_TTL_SECONDS`, `MATCHMAKING_BOT_WAIT_SECONDS`, `BOT_OPPONENT_ENABLED`, `BOT_SKILL`, `BOT_TURN_DELAY_SECONDS`, `FRONTEND_HOST`, SMTP settings, …) |
| `tests/conftest.py` | Env overrides before imports, per-test DB reset | fixtures incl. `inviter_headers`, `opponent_headers` |
