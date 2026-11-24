"""Generator prompts, schemas and drafting rules (app/vocab_pipeline/{prompts,drafting}.py)."""

import itertools
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest

from app.game.vocab_validation import entry_errors
from app.game.vocab_words import word_from_json
from app.vocab_pipeline.drafting import (
    allocate,
    backfill_entry,
    entry_from_output,
    practice_slot,
    select_candidates,
)
from app.vocab_pipeline.prompts import (
    BACKFILL_SCHEMA,
    DRAFT_EXAMPLE,
    DRAFT_SCHEMA,
    DRAFT_SYSTEM,
    PROPOSE_SCHEMA,
    draft_message,
)
from tests.vocab_helpers import entry, model_output, pending_draft


def walk(node: Any) -> Iterator[dict[str, Any]]:
    """Every dict in a JSON tree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_model_output_and_prompt_example_make_valid_entries():
    for word, output in (
        ("candid", model_output("candid")),
        ("discerning", DRAFT_EXAMPLE),
    ):
        draft = {**pending_draft(word), "entry": None, "status": "submitted"}
        built = entry_from_output(draft, output)
        assert entry_errors(word_from_json({**built, "id": 46})) == [], word


def test_entry_from_output_flattens_rotates_and_checks_the_word():
    draft = {**pending_draft("candid"), "entry": None}
    out = model_output("candid", alternatives=["Frank", "frank", "candid"])
    built = entry_from_output(draft, out)
    assert built["options"] == [o["sentence"] for o in out["room"]["options"]]
    assert built["reasons"] == [o["reason"] for o in out["room"]["options"]]
    slot = practice_slot("candid")
    assert built["practice"]["answer"] == slot
    correct = out["practice"]["options"][0]
    assert built["practice"]["options"][slot] == correct["sentence"]
    assert built["practice"]["reasons"][slot] == correct["reason"]
    # Deduped; validation reports "candid".
    assert built["alternatives"] == ["frank", "candid"]
    with pytest.raises(ValueError, match="model wrote candour, not candid"):
        entry_from_output(draft, {**out, "word": "candour"})


def test_backfill_entry_adds_practice_and_a_fourth_option():
    base = entry(3, "alpha")
    legacy = {
        **base,
        "options": base["options"][:3],
        "reasons": base["reasons"][:3],
        "practice": None,
    }
    out = {
        "practice": model_output("alpha")["practice"],
        "extra_option": {"sentence": base["options"][3], "reason": base["reasons"][3]},
    }
    built = backfill_entry(legacy, out)
    assert built["options"] == base["options"] and built["reasons"] == base["reasons"]
    assert built["practice"]["answer"] == practice_slot("alpha")
    untouched = ("options", "reasons", "practice")
    assert {k: v for k, v in built.items() if k not in untouched} == {
        k: v for k, v in legacy.items() if k not in untouched
    }
    four = backfill_entry({**base, "practice": None}, {**out, "extra_option": None})
    assert four["options"] == base["options"]
    with pytest.raises(ValueError, match="needs a fourth room option"):
        backfill_entry(legacy, {**out, "extra_option": None})


def test_allocate_moves_the_list_toward_the_target_mix():
    assert allocate({"Foundation": 8, "Intermediate": 18, "Advanced": 20}, 100) == {
        "Foundation": 36,
        "Intermediate": 40,
        "Advanced": 24,
    }
    assert allocate({"Foundation": 0, "Intermediate": 0, "Advanced": 100}, 10) == {
        "Foundation": 4,
        "Intermediate": 6,
        "Advanced": 0,
    }
    assert allocate({"Foundation": 5, "Intermediate": 5, "Advanced": 5}, 0) == {
        "Foundation": 0,
        "Intermediate": 0,
        "Advanced": 0,
    }


def test_select_candidates_dedupes_and_respects_the_quota():
    proposed = [
        {"word": "candid", "pos": "adjective", "difficulty": "Foundation"},
        {"word": "Candid", "pos": "adjective", "difficulty": "Foundation"},
        {"word": "lucid", "pos": "adjective", "difficulty": "Foundation"},
        {"word": "wane", "pos": "verb", "difficulty": "Intermediate"},
        {"word": "deft", "pos": "adjective", "difficulty": "Foundation"},
        {"word": "gist", "pos": "pronoun", "difficulty": "Intermediate"},
    ]
    got = select_candidates(
        proposed, {"Foundation": 1, "Intermediate": 2, "Advanced": 0}, {"lucid"}
    )
    assert [(d["slug"], d["difficulty"], d["status"]) for d in got] == [
        ("candid", "Foundation", "queued"),
        ("wane", "Intermediate", "queued"),
    ]


def test_practice_slots_are_pinned_and_spread():
    assert [practice_slot(s) for s in ("discerning", "equivocal", "pragmatic")] == [
        1,
        2,
        2,
    ]
    slots = Counter(
        practice_slot("".join(p)) for p in itertools.product("abcdefghij", repeat=3)
    )
    assert all(200 <= slots[i] <= 300 for i in range(4))


def test_schemas_follow_structured_output_rules():
    unsupported = {
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
    }
    for schema in (PROPOSE_SCHEMA, DRAFT_SCHEMA, BACKFILL_SCHEMA):
        for node in walk(schema):
            assert not unsupported & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
    # Well above the 512-token cache minimum.
    assert len(DRAFT_SYSTEM.split()) > 450
    advanced = {**pending_draft("alpha"), "pos": "noun", "difficulty": "Advanced"}
    assert "Target difficulty: Advanced." in draft_message(advanced)
