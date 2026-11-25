# The Vocab Challenger word list

Last updated: 2026-11-24

How words are stored, generated, approved and fixed. The code is `app/game/vocab_words.py`
(loading), `app/game/vocab_validation.py` (the rules) and `app/vocab_pipeline/` (the generator);
the plan that built it is webcade's `docs/plans/vocab-challenger-site/3-word-list.md`.

## How a word is stored

Each shipped word is one JSON file, `app/game/vocab_entries/{id:05d}-{slug}.json`, with the
keys of `FIELDS` in `vocab_words.py` and written by `dump_entry` (two-space indent, UTF-8, a
trailing newline). The files load once at import into `WORDS`; nothing reads them at request
time.

One file per word keeps a new word a new file in a PR diff, keeps reviews and merges small,
and lets CI check every word without a database or a migration.

- **Ids are permanent.** `WORDS[i].id == i`, dense from 0; wordbooks and the daily schedule
  store ids. Never renumber, rename or delete a shipped file. The loader raises on a gap, a
  duplicate id or slug, or a file name that doesn't match its contents.
- **Slugs are permanent** once shipped (piece 4 makes them public URLs). A spelling fix keeps
  the old slug.
- `options[0]` is the correct room answer; the room shuffles options per round. `practice` is
  a second question for the word's public page and is never used in a room. Every word has
  four room options and a practice question.

## The rules

`entry_errors` checks one word and `list_errors` the whole list: lengths (`LIMITS`), counts
(`OPTION_COUNT`, `SYNONYM_COUNT`, `MAX_ALTERNATIVES`), that each sentence uses the word, that
`options[0]` holds it exactly once (the blank round removes it), and that the practice
question doesn't paraphrase the room question or the example (`too_close`). Read the values
in `vocab_validation.py` rather than copying them here. `tests/test_vocab_words.py` and
`tests/test_vocab_validation.py` run them over every shipped file, so `make check` fails on a
bad word.

Validation checks shape, not meaning. A wrong definition or an arguable answer passes; that is
what player reports are for (below).

## Commands

Run from the repo root: `uv run python -m app.vocab_pipeline <command>`.

| Command | Does |
| ------- | ---- |
| `status` | Shipped count and difficulty mix against the 30/40/30 target, drafts by status, and the last date the daily word is fixed; warns when fewer than 30 days are left |
| `generate --count N [--yes]` | Drafts `N` words with `claude -p` on your Claude subscription, then approves every draft that passes validation. `--yes` skips the usage prompt |
| `approve --all` | Approves every pending draft that passes, as `generate` does at the end. Exits non-zero if any couldn't ship |
| `approve SLUG...` | Approves the named drafts |
| `review` | Walks drafts that need a person (invalid ones with an entry): approve, edit in `$EDITOR`, reject or skip |
| `reject SLUG --note TEXT` | Marks a draft rejected; the word is never drafted again |
| `requeue SLUG...` | Puts a draft back in the queue for the next `generate` |
| `fmt` | Rewrites shipped files that aren't in the canonical format, such as after a hand edit |
| `reports` | Players' reports of problems with words, grouped by word, most reported first |

Pipeline state lives in `vocab/` at the repo root and is committed: `vocab/drafts/<slug>.json`
(one per draft, with a status) and `vocab/wanted.json` (below). The app never loads it, and
`.dockerignore` keeps it out of the image.

## Generating words

`generate` runs on the owner's machine only. It needs Claude Code signed in to a Claude
subscription (`claude auth status`; sign in with `claude auth login` or `/login`). It never
uses an API key: `run_claude` removes `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the
child's environment, so a run can't bill the API. Each word is one `claude -p` call with
`--safe-mode` (no CLAUDE.md, skills, hooks or MCP servers) and no tools.

A run:

1. drafts any queued retries first;
2. asks the proposer for the rest, split across difficulties to move the list toward
   30/40/30, excluding every shipped and drafted word;
3. drafts each word and saves it as soon as it returns;
4. approves every draft that passes validation, in hashed order, so the ids it hands out mix
   difficulties in the daily schedule.

A word takes about 6,000 tokens of subscription usage and 25 seconds, so 100 words is about
600,000 tokens and 40 minutes, and may span more than one usage window. When a usage limit,
a sign-in problem or a timeout stops the CLI, the run stops cleanly, keeps everything finished
(and approves it), and prints `generate --count M` with the number still queued. Run exactly
that once it's fixed; the original count would propose new words on top. Ctrl-C is safe too.

**Wanted words.** When a word lists an alternative that doesn't ship yet, the alternative waits
in `vocab/wanted.json` with the words that want it, and shipping it links it back (up to three
alternatives per word). The proposer gets the wanted words only as suggestions, under the same
rules as any word: never an inflected or derived form of a shipped word, nothing a learner
would only meet as a wrong answer. They don't jump the queue.

## Approval and the PR

No person reviews each word: a draft that passes validation ships. The check is the PR. After
a run, commit `app/game/vocab_entries/` and `vocab/`, open a PR into `main`, and skim the new
entry files before it deploys. Each approval also touches the entries a wanted word links back
into, and `vocab/wanted.json`.

## Player reports

Round feedback in webcade has "Report a problem": a reason (`wrong_definition`,
`wrong_answer`, `confusing`, `inappropriate`, `other`), the question on screen, and an optional
note. Anyone can report, signed in or not, limited per network (`REPORTS_PER_NETWORK_PER_HOUR`
in `app/api/routes/vocab.py`). Reports land in the `vocab_word_report` table; no address is
stored.

To read them on the VM, run the `reports` command there with the production settings (from the
repo root: `APP_ENV=production uv run python -m app.vocab_pipeline reports`), or call
`GET /api/v1/vocab/reports` as a superuser. A reported word keeps showing. To fix one, edit its
entry file (never its `id` or `slug`), run `fmt` and `make check`, and ship it in a PR.

## The daily word

`daily_word(day)` takes the Pacific day. Before `DAILY_ANCHOR` (2026-12-06) it keeps the
original 46-word rotation; from the anchor on, ids show one per day in order. So a day's word
never changes as long as the list stays ahead of the calendar: adding words only fills days
that haven't happened. `status` prints the last fixed day and warns 30 days before the list
runs out; after that the schedule wraps, and wrapped days change when words are added later.
Keep generating before the warning.

## Two branches approving at once

Approval gives each new word the next free id, so two branches that both approve words both
write `00146-…`. After the first merges, the second fails `make check` with
`duplicate id 146: 00146-a.json, 00146-b.json`. On the unmerged branch, renumber its new files
to follow the merged ones: change each file's `id` and its file name's number together, keeping
the order. Then run `fmt` and `make check`. Only words not yet shown move, so no past day
changes.

## History

The first 46 words came from `~/projects/vocab-app` (`server/curriculum.ts`), which hasn't
changed since 2026-10-03 and isn't a source of truth: latterboard's entries have had fixes
since. All 46 later gained a practice question and a fourth room option through a one-off
backfill (removed once done; it's in git history).
