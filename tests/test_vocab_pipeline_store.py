"""The vocab draft store (app/vocab_pipeline/store.py): approving, rejecting and wanted words."""

import json

import pytest

from app.game.vocab_words import dump_entry
from app.vocab_pipeline.store import ApprovalError
from tests.vocab_helpers import entry, make_store, pending_draft


def test_approve_assigns_the_next_id_and_removes_the_draft(tmp_path):
    store = make_store(tmp_path, [entry(0, "alpha"), entry(1, "beta")])
    store.save_draft(pending_draft("gamma"))
    assert [p.name for p in store.approve("gamma")] == ["00002-gamma.json"]
    assert [w.slug for w in store.shipped()] == ["alpha", "beta", "gamma"]
    assert store.drafts() == []
    with pytest.raises(ApprovalError, match="no draft for gamma"):
        store.approve("gamma")


def test_approve_refuses_an_invalid_draft_and_writes_nothing(tmp_path):
    base = entry(0, "gamma")
    store = make_store(tmp_path, [entry(0, "alpha")])
    store.save_draft(
        pending_draft("gamma", options=base["options"][:3], reasons=base["reasons"][:3])
    )
    with pytest.raises(ApprovalError, match="options: expected 4"):
        store.approve("gamma")
    assert len(store.shipped()) == 1 and store.draft("gamma")["status"] == "pending"


def test_unshipped_alternatives_wait_as_wanted_words_and_link_back_when_they_ship(
    tmp_path,
):
    store = make_store(
        tmp_path, [entry(0, "alpha"), entry(1, "beta", alternatives=["alpha"])]
    )
    store.save_draft(pending_draft("gamma", alternatives=["alpha", "delta"]))
    store.approve("gamma")
    assert store.shipped()[2].alternatives == ("alpha",)
    assert store.wanted() == {"delta": {"word": "delta", "referrers": ["gamma"]}}
    store.save_draft({**pending_draft("delta"), "source": "wanted"})
    store.approve("delta")
    assert store.shipped()[2].alternatives == ("alpha", "delta")
    assert store.wanted() == {}


def test_reject_keeps_the_slug_known_and_requeue_resets_it(tmp_path):
    store = make_store(tmp_path, [entry(0, "alpha")])
    store.save_draft(pending_draft("gamma"))
    store.reject("gamma", "too obscure")
    assert store.draft("gamma")["status"] == "rejected"
    assert store.draft("gamma")["note"] == "too obscure"
    assert "gamma" in store.known_slugs() and store.reviewable() == []
    store.requeue("gamma")
    assert store.draft("gamma")["status"] == "queued"
    assert store.draft("gamma")["entry"] is None


def test_fmt_rewrites_hand_edited_files(tmp_path):
    store = make_store(tmp_path, [entry(0, "alpha")])
    path = tmp_path / "entries" / "00000-alpha.json"
    data = json.loads(path.read_text())
    path.write_text(json.dumps(dict(reversed(list(data.items()))), indent=4))
    assert store.fmt() == [path]
    assert path.read_text() == dump_entry(entry(0, "alpha"))
    assert store.fmt() == []
