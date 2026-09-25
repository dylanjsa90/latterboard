# Matches, matchmaking, and the computer opponent

Last updated: 2026-09-16

Rules for two-player play: game modes, match state, starting matches, invite links, the
matchmaking queue, and the bot. Endpoint and frame shapes are in the cross-repo contract in
`AGENTS.md`; module exports are in `docs/architecture/backend.md`.

## Modes and state

- `match.game` is one of: `wordle` (turn-based, shared board, the original mode), `word_race` / `cipher_race` (same private puzzle, played at the same time on separate boards, fewest attempts wins), or `sudoku_coop` (one shared board, shared mistake limit). The rules live in `app/game/match_modes.py`.
- Every move endpoint must call `_require_game`. Without it, a race match sent to `/guess` runs the turn-based logic against a `target_word` of `""`.
- `match.max_guesses` holds attempts per player in races, and the team's mistake limit in `sudoku_coop`.
- Race and co-op matches keep their private puzzle and live state in `match_puzzle`, not on `match`. It's a new table so `create_all` can add it to existing databases without a migration. Their `match.target_word` is `""` (the column is non-null).
- Change `match_puzzle` state only through `CRUDMatchPuzzle._apply`, and have the change function re-check `match.status` itself. `version` is an optimistic lock: when both players move at once, the losing write is replayed on fresh state instead of overwriting the other move.
- In races, never send a player's guess (letters/digits) to the opponent while the match is in progress. Send only grades, both in websocket frames and in `GET /matches/{id}`. The answer and guesses are revealed once `status == "completed"`.
- Match puzzles are freshly generated per match, not the daily `puzzle` rows. For sudoku use `generate_sudoku()`; `sudoku_variant()` only yields 9 distinct boards.
- A move and its broadcast live in `app/api/match_play.py`, shared by the routes and the bot so their frames can't drift. Nothing below the route layer raises `HTTPException`: inside a background task it reaches no handler. `MoveRejected` propagates instead, and the routes turn it into a 409.

## Starting matches

- Start a match between two known players only through `app/api/match_start.py`: `start_match` (reuses an open match the pair already shares, else creates and accepts one) and `notify_match_started` (the `match_started` frame to both). `/accept`, matchmaking, and invite-link claims all use it.
- Invite links (`match_invite_link`, `app/api/routes/invite_links.py`) are single-use and name no invitee until claimed; claiming makes the link's sender the match's inviter. Claims use a conditional UPDATE so two people can't both win one link.

## Matchmaking

- Matchmaking (`app/api/routes/matchmaking.py`) keeps one Redis sorted set per game (`mm:queue:{game}`), scored by each player's last join. `POST /matchmaking/{game}` atomically (Lua) pairs the caller with the longest-waiting other player or queues them; a pairing creates and accepts the match at once and sends the same `match_started` frame as `/accept`. Entries older than `MATCHMAKING_QUEUE_TTL_SECONDS` are skipped, so clients must re-POST to stay queued. That's how a closed tab leaves the queue.
- A companion hash `mm:waiting_since:{game}` holds each player's *first* join, written with `HSETNX`: the sorted set's score is the **last** join and is refreshed on every re-POST, so it can't also measure how long someone has waited. It is `HDEL`'d on pairing, on leaving, on the stale sweep and when switching games, and the key carries an `EXPIRE` in case a process dies in between.
- Tests clear `mm:queue:*` **and** `mm:waiting_since:*` themselves, since users are recreated with the same ids — a leftover waiting-since makes the `HSETNX` a no-op and the next test's player is handed a bot on their first call.

## The computer opponent

- A player nobody joins within `MATCHMAKING_BOT_WAIT_SECONDS` (20) is paired with the computer: a seeded `user` row with `is_bot` set (`init_db.seed_bot_user`). It is an ordinary participant — `match.inviter_id`/`invitee_id` are non-null FKs and nothing in the match code special-cases it. `is_bot` is **nullable** (that's the only way `ensure_user_columns` can add it), so read it as `bool(user.is_bot)`, never `is True`. `crud_user.bot_ids` / `any_are_bots` are the one place that answers "is this a bot?".
- Bot matches write **no** `game_score` rows and are excluded from the `record` in `/matches/me/history` (they still appear in `items`). Without those guards the bot itself climbs the leaderboard, and these writes bypass `POST /scores/` so its daily cap wouldn't catch it.
- The computer plays `wordle`, `word_race` and `cipher_race`, never `sudoku_coop` (cooperative and free-form). `POST /matches/invite` rejects it with 400: nothing would accept.
- The bot moves in the request's FastAPI `BackgroundTasks` (`app/api/bot_turn.py`), never a detached `asyncio.create_task` — background tasks are awaited inside the request cycle, so uvicorn's graceful shutdown waits for them and the sync `TestClient` runs them before returning. Tests set `BOT_TURN_DELAY_SECONDS=0` and get deterministic frame ordering with no test-only branch. It reacts one move per human move, in races too, so attempt counts stay level; it only plays itself out when the human has finished and the race can't otherwise resolve.
