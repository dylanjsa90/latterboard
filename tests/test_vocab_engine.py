"""Vocab Challenger's pure rules (app/game/vocab.py), ported from vocab-app's engine.test.ts."""

import copy

import pytest

from app.game import vocab
from app.game.vocab_words import WORDS


def player(player_id: str) -> vocab.Player:
    return vocab.new_player(player_id, player_id, now=0)


def duel() -> vocab.Game:
    g = vocab.make_game("ABC123", "duel", player("Alice"), now=1000)
    g["players"].append(player("Bob"))
    return g


def answer(
    g: vocab.Game, who: str, choice: int, now: int, round_index: int | None = None
) -> None:
    vocab.apply_action(
        g,
        who,
        "answer",
        now,
        choice=choice,
        round_index=g["round"] if round_index is None else round_index,
        generation=g["generation"],
    )


def test_word_ids_are_stable_and_games_mix_difficulties():
    assert WORDS[0].word == "discerning"
    assert WORDS[1].word == "equivocal"
    assert all(w.id == i for i, w in enumerate(WORDS))
    assert any(w.word == "perspicacious" for w in WORDS)
    g = duel()
    assert len(set(g["ids"])) == vocab.ROUNDS
    assert len({WORDS[i].difficulty for i in g["ids"]}) == 3


# One test rather than parametrized: conftest rebuilds the database before every test.
def test_every_question_has_aligned_reasons_one_key_and_a_blank():
    for word in WORDS:
        for r in range(vocab.ROUNDS):
            g = duel()
            g["ids"] = [word.id] * vocab.ROUNDS
            q = vocab.full_question(g, r)
            assert len(q["options"]) == len(q["reasons"]), word.word
            assert 0 <= q["correct"] < len(q["options"]), word.word
            assert len(set(q["options"])) == len(q["options"]), word.word
            if q["kind"] == "context":
                assert "_____" in q["prompt"], word.word
                assert q["options"][q["correct"]] == word.word
            else:
                assert q["options"][q["correct"]] == word.options[0]


def test_option_order_is_stable_across_requests():
    g = duel()
    assert vocab.full_question(g, 0) == vocab.full_question(g, 0)


def test_both_must_ready_and_unresolved_answers_stay_private():
    g = duel()
    vocab.apply_action(g, "Alice", "ready", 1100)
    assert g["phase"] == "lobby"
    vocab.apply_action(g, "Bob", "ready", 1200)
    assert g["phase"] == "question"
    answer(g, "Alice", vocab.full_question(g)["correct"], 1500)
    v = vocab.visible(g, "Bob")
    assert v["history"] == []
    assert v["mine"] is None
    for field in ("correct", "reasons", "example", "synonyms", "nuance"):
        assert field not in v["question"]
    assert v["players"][0]["score"] == 0
    assert v["players"][0]["answered"] is True
    with pytest.raises(vocab.VocabError, match="participant"):
        vocab.visible(g, "Mallory")
    g["round"] = 1
    for p in g["players"]:
        p["answers"] = {}
    context = vocab.visible(g, "Bob")["question"]
    assert context["kind"] == "context"
    assert context["word"] is None
    assert context["definition"] is None
    assert context["id"] is None


def test_bad_answers_cannot_score():
    g = vocab.make_game("ABC123", "solo", player("Alice"), now=1000)
    with pytest.raises(vocab.VocabError, match="participant") as e:
        answer(g, "Mallory", 0, 1100)
    assert e.value.status == 403
    with pytest.raises(vocab.VocabError, match="available"):
        answer(g, "Alice", 99, 1100)
    with pytest.raises(vocab.VocabError, match="ended"):
        vocab.apply_action(
            g, "Alice", "answer", 1100, choice=0, round_index=0, generation=1
        )
    with pytest.raises(vocab.VocabError, match="ended"):
        answer(g, "Alice", 0, 1100, round_index=1)
    key = vocab.full_question(g)["correct"]
    answer(g, "Alice", key, 1100)
    with pytest.raises(vocab.VocabError, match="ended|locked"):
        answer(g, "Alice", key, 1200, round_index=0)
    assert g["players"][0]["answers"]["0"]["points"] == 105

    expired = vocab.make_game("DEF456", "solo", player("Alice"), now=1000)
    with pytest.raises(vocab.VocabError, match="ended"):
        answer(expired, "Alice", 0, 1000 + vocab.ROUND_MS)
    assert expired["players"][0]["answers"] == {}


def test_five_round_duel_resolves_deadlines_scores_and_needs_mutual_rematch():
    g = duel()
    vocab.apply_action(g, "Alice", "ready", 1000)
    vocab.apply_action(g, "Bob", "ready", 1000)
    now = 1000
    for _ in range(vocab.ROUNDS):
        q = vocab.full_question(g)
        answer(g, "Alice", q["correct"], now + 30000)
        answer(g, "Bob", (q["correct"] + 1) % len(q["options"]), now + 30001)
        assert g["phase"] == "feedback"
        vocab.advance(g, now + 30001 + vocab.FEEDBACK_MS)
        now += 30001 + vocab.FEEDBACK_MS
    assert g["phase"] == "results"
    v = vocab.visible(g, "Alice")
    assert len(v["history"]) == vocab.ROUNDS
    assert v["players"][0]["score"] == 510
    assert v["players"][1]["score"] == 0
    vocab.apply_action(g, "Alice", "rematch", now + 1)
    assert g["phase"] == "results"
    assert vocab.visible(g, "Alice")["rematch_requested"] is True
    vocab.apply_action(g, "Bob", "rematch", now + 2)
    assert g["phase"] == "lobby"
    assert g["generation"] == 1
    assert g["players"][0]["answers"] == {}
    assert not any(p["ready"] for p in g["players"])


def test_unanswered_round_resolves_at_its_deadline():
    g = duel()
    vocab.apply_action(g, "Alice", "ready", 1000)
    vocab.apply_action(g, "Bob", "ready", 1000)
    vocab.advance(g, 1000 + vocab.ROUND_MS - 1)
    assert g["phase"] == "question"
    vocab.advance(g, 1000 + vocab.ROUND_MS)
    assert g["phase"] == "feedback"
    assert g["deadline"] == 1000 + vocab.ROUND_MS + vocab.FEEDBACK_MS


def test_changed_ignores_seen_but_not_play():
    g = duel()
    before = copy.deepcopy(g)
    vocab.touch(g, "Alice", 5000)
    assert not vocab.changed(before, g)

    vocab.apply_action(g, "Alice", "ready", 5000)
    assert vocab.changed(before, g)

    vocab.apply_action(g, "Bob", "ready", 5000)
    before = copy.deepcopy(g)
    answer(g, "Alice", 0, 6000)
    assert vocab.changed(before, g)

    before = copy.deepcopy(g)
    vocab.advance(g, g["deadline"])
    assert g["phase"] == "feedback"
    assert vocab.changed(before, g)


def test_solo_feedback_waits_and_next_only_advances_resolved_rounds():
    g = vocab.make_game("ABC123", "solo", player("Alice"), now=1000)
    with pytest.raises(vocab.VocabError, match="finish"):
        vocab.apply_action(g, "Alice", "next", 1001)
    answer(g, "Alice", vocab.full_question(g)["correct"], 2000)
    vocab.advance(g, 100000)
    assert g["phase"] == "feedback"
    vocab.apply_action(g, "Alice", "next", 100000)
    assert g["round"] == 1
    assert g["deadline"] == 100000 + vocab.ROUND_MS


def test_unknown_action_is_rejected():
    g = duel()
    with pytest.raises(vocab.VocabError, match="Unknown"):
        vocab.apply_action(g, "Alice", "teleport", 1000)


def test_review_ladder():
    cases = [
        (0, True, 1, 1),
        (1, True, 2, 3),
        (5, True, 6, 60),
        (6, True, 6, 60),
        (4, False, 0, 10 / 1440),
    ]
    for level, known, expected_level, expected_days in cases:
        new_level, wait = vocab.next_review(level, known)
        assert new_level == expected_level
        assert wait.total_seconds() == pytest.approx(expected_days * 86400)
