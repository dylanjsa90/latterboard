"""The word-list commands (app/vocab_pipeline/cli.py): review, status and main."""

import io
import json
from datetime import timedelta

from app.game.vocab_words import DAILY_ANCHOR
from app.vocab_pipeline.cli import main, render, review, status
from tests.vocab_helpers import entry, make_store, pending_draft


def test_render_marks_answers_and_unshipped_alternatives():
    d = pending_draft("gamma", alternatives=["alpha", "delta"])
    text = render(d, {"alpha"}, ["gamma: options[2]: 121 characters (max 120)"])
    e = d["entry"]
    assert "A ✓ " + e["options"][0] in text and "B   " + e["options"][1] in text
    p = e["practice"]
    assert f"{'ABCD'[p['answer']]} ✓ {p['options'][p['answer']]}" in text
    assert "alpha (shipped)" in text and "delta (not shipped yet" in text
    assert "Problems:" in text and "121 characters" in text


def test_review_approves_rejects_and_edits(tmp_path):
    store = make_store(tmp_path, [entry(0, "alpha")])
    store.save_draft(pending_draft("beta"))
    store.save_draft(pending_draft("gamma"))
    long = entry(0, "delta")
    long["options"][2] = "delta " + "x" * 115
    store.save_draft(
        {
            **pending_draft("delta"),
            "status": "invalid",
            "entry": {k: v for k, v in long.items() if k != "id"},
        }
    )
    script = {"beta": ["a"], "gamma": ["r", "too obscure"], "delta": ["e", "a"]}

    def ask(prompt):
        # Every prompt starts "{slug}: ", so the review order doesn't matter.
        return script[prompt.split(":")[0]].pop(0)

    def edit(path):
        # The reviewer shortens the long option.
        d = json.loads(path.read_text())
        d["entry"]["options"][2] = entry(0, "delta")["options"][2]
        path.write_text(json.dumps(d))

    tally = review(store, io.StringIO(), ask=ask, edit=edit)
    assert tally == {"approved": 2, "rejected": 1, "skipped": 0}
    assert sorted(w.slug for w in store.shipped()) == ["alpha", "beta", "delta"]
    assert store.draft("gamma")["note"] == "too obscure"


def test_status_reports_counts_mix_and_runway(tmp_path):
    store = make_store(
        tmp_path, [entry(0, "alpha"), entry(1, "beta", difficulty="Intermediate")]
    )
    store.save_draft(pending_draft("gamma"))
    out = io.StringIO()
    status(store, DAILY_ANCHOR, out)
    text = out.getvalue()
    assert (
        "Shipped: 2 (Foundation 1, Intermediate 1, Advanced 0; target 30/40/30)" in text
    )
    assert "Drafts: 0 queued, 1 pending, 0 invalid, 0 rejected" in text
    assert f"Daily words fixed through {DAILY_ANCHOR + timedelta(days=1)}" in text
    assert "Warning: only 1 days of daily words left" in text


def test_main_runs_store_commands(tmp_path):
    store = make_store(tmp_path, [entry(0, "alpha")])
    store.save_draft(pending_draft("beta"))
    assert main(["approve", "beta"], store=store, out=io.StringIO()) == 0
    assert main(["approve", "nope"], store=store, out=io.StringIO()) == 1
    assert main(["fmt"], store=store, out=io.StringIO()) == 0
