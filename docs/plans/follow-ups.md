# Follow-ups from the 2026-11 repo review

Last updated: 2026-11-13

Known issues the polish pass (startup and logging fixes, email privacy, repo hygiene,
Alembic, the `app/` restructure, tests, docs) found but deliberately left alone. Each is
small enough to be its own change. Delete an item when it ships; delete this file when it's
empty.

## Security

- **Require the `purpose` claim on access tokens.** Access tokens issued before it existed
  have none, and `deps._access_token_user_id` still accepts that so nobody was signed out.
  Once they have all expired (`ACCESS_TOKEN_EXPIRE_MINUTES`, 8 days after the deploy that
  added it), reject a missing `purpose` too.

## Bugs and gaps

- **The recovery email links to a page webcade doesn't have.** It points at
  `{FRONTEND_HOST}/reset-password?token=…`, which webcade serves as a 404. Either build the
  page in webcade (`POST /reset-password/` is ready) or hide recovery until it exists.
- **The optional compose Postgres can't be reached.** `DATABASE_URL` always adds
  `sslmode=require`, and `postgres:16-alpine` has no SSL, so `--profile local-db` fails to
  connect. Add a `DB_SSLMODE` setting (default `require`). Production uses SQLite, so this
  only affects trying Postgres.

## Code health

- **The tests' `TestClient` warns** that Starlette wants `httpx2` instead of `httpx`.
- **No error reporting.** `sentry-sdk` was removed as unused (it's still installed through
  `fastapi[standard]`); nothing reports exceptions beyond the container log.
