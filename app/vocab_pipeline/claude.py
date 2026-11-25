"""Drafting words through Claude Code's headless mode (`claude -p`) on the owner's Claude
subscription. The only module that runs `claude`.

Each word is one `claude -p` call with the plan's system prompt and a JSON schema, and is
saved as soon as it returns, so a usage limit, a crash or Ctrl-C loses nothing finished.
The child never sees an API key, so a run can't bill the API by accident.
"""

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import Any, TextIO

from app.game.vocab_words import DIFFICULTIES, Difficulty
from app.vocab_pipeline.drafting import allocate, entry_from_output, select_candidates
from app.vocab_pipeline.prompts import (
    DRAFT_SCHEMA,
    DRAFT_SYSTEM,
    PROPOSE_SCHEMA,
    PROPOSE_SYSTEM,
    draft_message,
    propose_message,
)
from app.vocab_pipeline.store import Draft, Store

MODEL = "claude-opus-5-5"
EFFORT = "high"
MAX_ATTEMPTS = 3
# Measured 2026-11-24: about 3,600 tokens in and 2,500 out, and 25 seconds, per word.
EST_TOKENS_PER_WORD = 6_000
TIMEOUT_SECONDS = 600
# An API key would bill the API instead of the subscription; CLAUDECODE is set inside a
# Claude Code session and would make the child treat itself as nested.
_DROPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")

# (system prompt, user message, JSON schema) -> the schema-validated output.
Runner = Callable[[str, str, Mapping[str, Any]], dict[str, Any]]


class RunError(Exception):
    """`claude` failed (not signed in, a usage limit, a timeout), so the run stops."""


def claude_command(system: str, schema: Mapping[str, Any]) -> list[str]:
    """A one-shot print session with only our prompt: --safe-mode drops CLAUDE.md, skills,
    hooks and MCP servers but keeps the sign-in (--bare would demand an API key), and
    --tools "" leaves the model nothing to do but write."""
    return [
        "claude",
        "-p",
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--safe-mode",
        "--tools",
        "",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--system-prompt",
        system,
        "--json-schema",
        json.dumps(schema),
    ]


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else ""


def parse_result(stdout: str) -> dict[str, Any]:
    """The structured output from a `--output-format json` result. An error result (marked
    by is_error; its subtype still says "success") raises RunError; a result without
    structured output, such as a refusal, raises ValueError."""
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RunError(f"unreadable output from claude: {stdout[:200]}") from e
    message = str(result.get("result") or "")
    if result.get("is_error"):
        raise RunError(_first_line(message) or "claude reported an error")
    output = result.get("structured_output")
    if not isinstance(output, dict):
        raise ValueError(f"no structured output: {message[:200]}")
    return output


def _failure(proc: "subprocess.CompletedProcess[str]") -> str:
    """The readable reason a run failed: the JSON result's message, else stderr."""
    try:
        message = _first_line(str(json.loads(proc.stdout).get("result") or ""))
    except (json.JSONDecodeError, AttributeError):
        message = ""
    return (
        message or _first_line(proc.stderr) or f"claude exited with {proc.returncode}"
    )


def run_claude(system: str, message: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    """The real Runner: one `claude -p` call, the message on stdin."""
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    try:
        proc = subprocess.run(
            claude_command(system, schema),
            input=message,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=env,
        )
    except FileNotFoundError as e:
        raise RunError("claude is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RunError(f"claude took longer than {TIMEOUT_SECONDS}s") from e
    if proc.returncode != 0:
        raise RunError(_failure(proc))
    return parse_result(proc.stdout)


def propose(run: Runner, store: Store, quota: Mapping[Difficulty, int]) -> list[Draft]:
    """Ask for new headwords and save the ones select_candidates keeps, as queued.
    Wanted words (alternatives entries list that don't ship yet) go along only as
    suggestions, so they face the same rules and checks as any proposal."""
    known = store.known_slugs()
    exclude = {w.word for w in store.shipped()} | {d["word"] for d in store.drafts()}
    suggest = [w["word"] for slug, w in store.wanted().items() if slug not in known]
    message = propose_message(quota, sorted(exclude), suggest)
    output = run(PROPOSE_SYSTEM, message, PROPOSE_SCHEMA)
    drafts = select_candidates(output["words"], quota, known)
    for d in drafts:
        store.save_draft(d)
    return drafts


def apply_output(store: Store, d: Draft, output: Mapping[str, Any]) -> Draft:
    """Turn a model output into the draft's entry and save it: pending when it passes
    validation, invalid otherwise (an entry that built is kept for the reviewer)."""
    try:
        built = entry_from_output(d, output)
    except ValueError as e:
        d = {**d, "status": "invalid", "entry": None, "errors": [str(e)]}
    else:
        d = {
            **d,
            "entry": built,
            "pos": built["pos"],
            "difficulty": built["difficulty"],
        }
        errors = store.draft_errors(d)
        d = {**d, "status": "invalid" if errors else "pending", "errors": errors}
    store.save_draft(d)
    return d


def draft_one(run: Runner, store: Store, d: Draft) -> Draft:
    """Draft one word. A RunError propagates and leaves the draft as it was."""
    try:
        output = run(DRAFT_SYSTEM, draft_message(d), DRAFT_SCHEMA)
    except ValueError as e:
        d = {
            **d,
            "attempts": d["attempts"] + 1,
            "status": "invalid",
            "entry": None,
            "errors": [str(e)],
        }
        store.save_draft(d)
        return d
    return apply_output(store, {**d, "attempts": d["attempts"] + 1}, output)


def _retryable(d: Draft) -> bool:
    return d["status"] == "queued" or (
        d["status"] == "invalid" and d["entry"] is None and d["attempts"] < MAX_ATTEMPTS
    )


def generate(
    run: Runner,
    store: Store,
    count: int,
    out: TextIO,
    confirm: Callable[[str], bool],
) -> int:
    """Draft `count` words: retries first, then newly proposed ones, then approve what
    passes. Returns how many were drafted. A RunError stops the run and says how to
    carry on."""
    if count <= 0:
        out.write("nothing to draft\n")
        return 0
    retries = [d for d in store.drafts() if _retryable(d)][:count]
    to_propose = count - len(retries)
    if not confirm(
        f"Draft {count} words with {MODEL} on your Claude subscription, "
        f"about {count * EST_TOKENS_PER_WORD:,} tokens of usage?"
    ):
        return 0

    queue = list(retries)
    drafted = 0
    try:
        if to_propose > 0:
            open_drafts = [d for d in store.drafts() if d["status"] != "rejected"]
            counts = {
                diff: sum(w.difficulty == diff for w in store.shipped())
                + sum(d["difficulty"] == diff for d in open_drafts)
                for diff in DIFFICULTIES
            }
            queue += propose(run, store, allocate(counts, to_propose))
        for d in queue:
            d = draft_one(run, store, d)
            drafted += 1
            out.write(f"{d['slug']}: {d['status']}\n")
    except RunError as e:
        queued = sum(_retryable(d) and d["status"] == "queued" for d in store.drafts())
        out.write(
            f"stopped: {e}. {drafted} drafted, {queued} still queued; once it is fixed "
            f"or the usage limit resets, run generate --count {queued} to carry on.\n"
        )
    approve_ready(store, out)
    return drafted


def approve_ready(store: Store, out: TextIO) -> dict[str, str]:
    """Ship every pending draft that passes validation, and say what happened. Returns
    the drafts that couldn't ship, with their errors; they stay behind as invalid."""
    approved, failed = store.approve_ready()
    if approved:
        out.write(f"approved {len(approved)}: {', '.join(approved)}\n")
    for slug, errors in failed.items():
        out.write(f"not approved {slug}:\n{errors}\n")
    return failed
