"""Maintainer-only tools that grow the Vocab Challenger word list.

Run on the maintainer's machine (`uv run python -m app.vocab_pipeline …`), never by the
app. Drafts live in `vocab/` at the repo root and never load at runtime; a word ships only
when a person approves its draft, which writes a file into `app/game/vocab_entries/`.
"""
