"""The word-list commands: `uv run python -m app.vocab_pipeline <command>`.

status, generate, review, approve, reject, requeue and fmt. `generate` runs `claude -p` on
the owner's Claude subscription (see claude.py); everything else is local.
"""

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import TextIO, TypedDict

from app.game.puzzles import today
from app.game.vocab_words import DAILY_ANCHOR, DIFFICULTIES, daily_runway
from app.vocab_pipeline import claude
from app.vocab_pipeline.drafting import TARGET_MIX
from app.vocab_pipeline.store import ApprovalError, Draft, Store

LETTERS = "ABCD"


class ReviewTally(TypedDict):
    approved: int
    rejected: int
    skipped: int


def _options(
    sentences: Sequence[str], reasons: Sequence[str], answer: int, new: int | None
) -> list[str]:
    lines = []
    for i, (sentence, reason) in enumerate(zip(sentences, reasons, strict=False)):
        mark = "✓" if i == answer else " "
        tag = " (new)" if i == new else ""
        lines += [f"  {LETTERS[i]} {mark} {sentence}{tag}", f"      {reason}"]
    return lines


def render(
    d: Draft,
    shipped_slugs: Collection[str],
    errors: Sequence[str],
    *,
    new_room_option: bool = False,
) -> str:
    """A draft as the reviewer sees it. `new_room_option` marks a backfill's 4th room
    option as new, for a shipped word that had only three."""
    e = d["entry"]
    assert e is not None
    backfill = d["source"] == "backfill"
    alternatives = ", ".join(
        f"{a} (shipped)"
        if a in shipped_slugs
        else f"{a} (not shipped yet: waits as a wanted word)"
        for a in e["alternatives"]
    )
    p = e["practice"]
    lines = [
        f"{e['word']} ({e['pos']}, {e['difficulty']}) {e['pronunciation']}  "
        f"[{d['source']}, attempt {d['attempts']}]",
        f"Definition: {e['definition']}",
        f"Example: {e['example']}",
        f"Synonyms: {', '.join(e['synonyms'])}",
        f"Nuance: {e['nuance']}",
        f"Alternatives: {alternatives or 'none'}",
        f"Room question: {e['prompt']}",
        *_options(e["options"], e["reasons"], 0, 3 if new_room_option else None),
        f"Practice question: {p['prompt']}{' (new)' if backfill else ''}",
        *_options(p["options"], p["reasons"], p["answer"], None),
    ]
    if errors:
        lines += ["Problems:", *(f"  - {error}" for error in errors)]
    return "\n".join(lines) + "\n"


def review_order(drafts: Iterable[Draft]) -> list[Draft]:
    """Backfills by id, then the rest in hashed order, so the ids approval hands out
    interleave difficulties in the daily walk."""

    def key(d: Draft) -> tuple[int, int, str]:
        if d["source"] == "backfill" and d["entry"] is not None:
            return (0, d["entry"]["id"], "")
        return (1, 0, hashlib.sha256(d["slug"].encode()).hexdigest())

    return sorted(drafts, key=key)


def open_in_editor(path: Path) -> None:
    subprocess.run(
        [*shlex.split(os.environ.get("EDITOR", "vi")), str(path)], check=False
    )


def _review_one(
    store: Store,
    slug: str,
    out: TextIO,
    ask: Callable[[str], str],
    edit: Callable[[Path], None],
    tally: ReviewTally,
) -> bool:
    """Review one draft until it is decided; False means the reviewer quit."""
    show = True
    while True:
        if show:
            d = store.draft(slug)
            shipped = {w.slug: w for w in store.shipped()}
            new_option = (
                d["source"] == "backfill"
                and slug in shipped
                and len(shipped[slug].options) == 3
            )
            errors = store.draft_errors(d)
            out.write(render(d, shipped.keys(), errors, new_room_option=new_option))
            show = False
        choice = ask(f"{slug}: [a]pprove [e]dit [r]eject [s]kip [q]uit > ")
        choice = choice.strip().lower()[:1]
        if choice == "a":
            try:
                paths = store.approve(slug)
            except ApprovalError as e:
                out.write(f"Not approved:\n{e}\n")
                continue
            tally["approved"] += 1
            out.write(f"approved: {', '.join(p.name for p in paths)}\n")
            return True
        if choice == "e":
            edit(store.draft_path(slug))
            try:
                d = store.draft(slug)
            except ValueError as e:
                out.write(f"Could not read the edited draft: {e}\n")
                continue
            errors = store.draft_errors(d)
            store.save_draft({**d, "status": "invalid" if errors else "pending"})
            show = True
        elif choice == "r":
            store.reject(slug, ask(f"{slug}: reason: ").strip())
            tally["rejected"] += 1
            return True
        elif choice == "s":
            tally["skipped"] += 1
            return True
        elif choice == "q":
            return False


def review(
    store: Store,
    out: TextIO,
    ask: Callable[[str], str] = input,
    edit: Callable[[Path], None] = open_in_editor,
) -> ReviewTally:
    tally: ReviewTally = {"approved": 0, "rejected": 0, "skipped": 0}
    for d in review_order(store.reviewable()):
        if not _review_one(store, d["slug"], out, ask, edit, tally):
            break
    out.write(
        f"{tally['approved']} approved, {tally['rejected']} rejected, "
        f"{tally['skipped']} skipped.\n"
        "Commit app/game/vocab_entries/ and vocab/, then open a PR into main so the "
        "diff gets a second look.\n"
    )
    return tally


def status(store: Store, today: date, out: TextIO) -> None:
    shipped = store.shipped()
    mix = Counter(w.difficulty for w in shipped)
    target = "/".join(str(round(TARGET_MIX[d] * 100)) for d in DIFFICULTIES)
    states = Counter(d["status"] for d in store.drafts())
    fixed_through = DAILY_ANCHOR + timedelta(days=len(shipped) - 1)
    runway = daily_runway(today, len(shipped))
    out.write(
        f"Shipped: {len(shipped)} ("
        + ", ".join(f"{d} {mix[d]}" for d in DIFFICULTIES)
        + f"; target {target})\n"
        f"Drafts: {states['queued']} queued, {states['pending']} pending, "
        f"{states['invalid']} invalid, {states['rejected']} rejected\n"
        f"Daily words fixed through {fixed_through}\n"
    )
    if runway < 30:
        out.write(
            f"Warning: only {runway} days of daily words left; ship more words before "
            f"{fixed_through + timedelta(days=1)} or days after it will change.\n"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.vocab_pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="counts, difficulty mix and daily-word runway")
    generate = commands.add_parser(
        "generate", help="draft new words with claude -p on your Claude subscription"
    )
    generate.add_argument("--count", type=int, required=True)
    generate.add_argument(
        "--yes", action="store_true", help="skip the usage prompt (owner agreed)"
    )
    commands.add_parser("review", help="approve, edit or reject drafts one by one")
    approve = commands.add_parser("approve", help="ship drafts without the review loop")
    approve.add_argument("slugs", nargs="+")
    reject = commands.add_parser(
        "reject", help="reject a draft so it is never redrafted"
    )
    reject.add_argument("slug")
    reject.add_argument("--note", required=True)
    requeue = commands.add_parser("requeue", help="put drafts back in the queue")
    requeue.add_argument("slugs", nargs="+")
    commands.add_parser("fmt", help="rewrite shipped files in the canonical format")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    store: Store | None = None,
    out: TextIO = sys.stdout,
) -> int:
    args = _parser().parse_args(argv)
    store = store or Store()
    try:
        if args.command == "status":
            status(store, today(), out)
        elif args.command == "generate":

            def confirm(prompt: str) -> bool:
                return (
                    bool(args.yes) or input(f"{prompt} [y/N] ").strip().lower() == "y"
                )

            claude.generate(claude.run_claude, store, args.count, out, confirm)
        elif args.command == "review":
            review(store, out)
        elif args.command == "approve":
            for slug in args.slugs:
                paths = store.approve(slug)
                out.write(f"approved {slug}: {', '.join(p.name for p in paths)}\n")
        elif args.command == "reject":
            store.reject(args.slug, args.note)
        elif args.command == "requeue":
            for slug in args.slugs:
                store.requeue(slug)
        elif args.command == "fmt":
            for path in store.fmt():
                out.write(f"rewrote {path.name}\n")
    except ApprovalError as e:
        out.write(f"error: {e}\n")
        return 1
    except KeyError as e:
        out.write(f"error: {e.args[0]}\n")
        return 1
    return 0
