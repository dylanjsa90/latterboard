# Cross-repo contract

Last updated: 2026-10-03

webcade (`~/projects/webcade`, React) is latterboard's (`~/projects/latterboard`, FastAPI) only
client. This file is `docs/reference/cross-repo-contract.md` in both repos: keep the two copies
identical, and update both when a shape changes.

- REST is under `/api/v1` (webcade: `API_V1_URL`). The websocket is at the app root, `/ws/{topic}`,
  **not** under `/api/v1`.
- Wire JSON is snake_case; webcade converts to camelCase inside each `api.ts`.
- Errors are FastAPI `{detail: string}`; webcade shows `detail` when it's a string.
- Authenticated calls send `Authorization: Bearer <access_token>` via webcade's `authFetch`; a 401
  ends the webcade session app-wide.
- `Grade` = `"correct" | "present" | "absent"`. Sudoku boards are `int[81]`, `0` = empty.

<!-- prettier-ignore-start -->

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
| `POST /word/guess` | `{puzzle_id, guess (5 chars), attempt_count}` | `{grades, won, lost, answer?}`; 409 once the signed-in player's word has an outcome |
| `GET /sudoku` | — | `{puzzle_id, puzzle: int[81], won, board: int[81]?}`; `board` (the solution) only when the signed-in player already won |
| `POST /sudoku/move` | `{puzzle_id, index 0-80, value 1-9, board}` | `{correct, completed}` |
| `POST /sudoku/hint` | `{puzzle_id, index, board}` | `{value, completed}` |
| `GET /memory` | — | `{puzzle_id, size}`; `puzzle_id` is `memory-{today}`, one deck per day |
| `POST /memory/reveal` | `{puzzle_id, index}` | `{symbol}`; 400 for an undated id |
| `GET /cipher` | — | `{puzzle_id, slots, max_attempts, initial_attempt: int[], initial_feedback, attempts: [{attempt, exact, close}], won, lost, answer?: int[]}` |
| `POST /cipher/attempt` | `{puzzle_id, attempt: int[4], attempt_count?}` | `{exact, close, won, lost, answer?: int[]}`; 409 once the signed-in player's cipher has an outcome |
| `GET /me/stats` | — | `{total_solved, today_solved, streak, best_streak, games: [{game, played, won}], recent: [{game, solved_on, solved_at, result}]}` |
| `GET /me/history?skip&limit` | — | `{items: [{game, puzzle_id, won, attempt_count, completed_at}], total}` |

The server decides word, sudoku, and cipher outcomes (webcade ADR 004). `attempt_count` counts
board rows including the fixed starter and the move being sent (`max_attempts` rows in all), and
is used only for guests: a signed-in player's moves are counted by the server, which also
returns them on `GET` so word and cipher resume. Stats and history include lost puzzles
(`won: false`). Memory's outcome stays on the client.

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

### Vocab Challenger (`/vocab`, bearer)
Five-round solo sessions and two-player duels, addressed by a six-character hex invite `code`
(case-insensitive in paths). Rooms aren't matches and have no socket: webcade polls `GET
/rooms/{code}` every 1.2 s, and every room call applies any transition that's due (a round ends
at its `deadline` or once everyone answered; duel feedback ends 16 s later). Times are **epoch
ms**; compare `deadline` and `seen` against `server_now`, not the device clock. Rooms expire
24 h after creation (410).

| Call | Request | Response |
| ---- | ------- | -------- |
| `POST /rooms` | `{mode: solo \| duel}` | 201 `RoomSnapshot`; solo starts at round 0, a duel waits in `lobby` |
| `GET /rooms/{code}` | — | `RoomSnapshot`; also records the caller's `seen`; 403 not a participant, 404, 410 |
| `POST /rooms/{code}/join` | — | `RoomSnapshot`; rejoining a room you're in just reconnects; 403 solo, 409 full or started |
| `POST /rooms/{code}/actions` | `{action: ready \| answer \| next \| rematch, generation, round?, choice?}` | `RoomSnapshot`; `ready` toggles; `answer` needs `round` and `choice` (409 stale round or generation, already answered, or past the deadline; 400 bad choice); `next` is solo feedback only; `rematch` restarts once every player asked |
| `GET /words` | — | `{saved: SavedWord[] (soonest due first), word_count}` |
| `PUT /words/{id}` / `DELETE /words/{id}` | — | 204; saving again changes nothing; 400 unknown word |
| `POST /words/{id}/review` | `{known: bool}` | 204; known moves up the ladder (due in 1, 3, 7, 14, 30, 60 days), a miss resets to level 0 (due in 10 min); 404 not saved |

- `RoomSnapshot` = `{game: GameView, server_now}`.
- `GameView` = `{code, mode, phase: lobby | question | feedback | results, round (0-4), generation, deadline, revision, rematch_requested, players: Seat[], question, mine?, history: [{question (revealed), answers: [{name, choice?, points?, correct?, ms?}]}]}`. `revision` only grows: drop a reply older than the one you have.
- `Seat` = `{id: "me" | "opponent", name (username), ready, seen, answered, score}`. Scores count only resolved rounds.
- `question` = `{id, word, pos, definition, difficulty: Foundation | Intermediate | Advanced, kind: usage | context, prompt, options, correct, reasons, example, synonyms, nuance}`. Until the round resolves (`phase` feedback or results) `correct` through `nuance` are null, and so are `id`, `word`, `pos`, `definition` for a `context` question, whose answer is the word.
- `SavedWord` = `{id, word, pos, definition, difficulty, example, synonyms, nuance, due, level (0-6)}`. Word ids are stable: wordbooks store them.
- A correct answer scores 100 plus `max(0, 5 - floor(ms / 8000))` speed points, `ms` counted from the round start; wrong or missing answers score 0.

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

<!-- prettier-ignore-end -->
