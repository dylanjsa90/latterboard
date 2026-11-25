"""Vocab Challenger's pure rules (app/game/vocab.py), ported from vocab-app's engine.test.ts."""

import copy
from datetime import date, timedelta

import pytest

from app.game import vocab
from app.game.vocab_words import (
    BY_SLUG,
    DAILY_ANCHOR,
    LEGACY_DAILY_COUNT,
    WORDS,
    daily_id,
    daily_runway,
    daily_word,
)


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


def test_daily_word_matches_the_original_rotation_while_the_list_has_46_words():
    assert DAILY_ANCHOR.toordinal() % LEGACY_DAILY_COUNT == 0
    for offset in range(-400, 400):
        day = DAILY_ANCHOR + timedelta(days=offset)
        assert daily_id(day, 46) == day.toordinal() % 46, day


def test_appending_words_never_changes_a_day_already_scheduled():
    for count in (46, 47, 146):
        days = [DAILY_ANCHOR + timedelta(days=o) for o in range(-200, count)]
        assert [daily_id(d, count) for d in days] == [daily_id(d, 5000) for d in days]
    assert daily_id(DAILY_ANCHOR + timedelta(days=46), 47) == 46
    week = [daily_id(DAILY_ANCHOR + timedelta(days=o), 146) for o in range(40, 47)]
    assert week == [40, 41, 42, 43, 44, 45, 46]
    assert daily_runway(DAILY_ANCHOR, 46) == 45
    assert daily_runway(DAILY_ANCHOR + timedelta(days=46), 46) == -1
    assert daily_runway(DAILY_ANCHOR + timedelta(days=46), 146) == 99
    # Literal, from the original formula: date(2026, 10, 5).toordinal() % 46 == 30.
    assert daily_word(date(2026, 10, 5)) == WORDS[30]


def test_every_word_has_a_pronunciation():
    for word in WORDS:
        assert len(word.pronunciation) > 2, word.word
        assert word.pronunciation.startswith("/"), word.word
        assert word.pronunciation.endswith("/"), word.word


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
                assert len(q["options"]) == 3, word.word
            else:
                assert q["options"][q["correct"]] == word.options[0]


def test_rematch_deals_words_the_room_has_not_played():
    for _ in range(50):
        first = vocab._pick_words()
        second = vocab._pick_words(exclude=first)
        assert len(set(second)) == vocab.ROUNDS and not set(first) & set(second)
        assert len({WORDS[i].difficulty for i in second}) == 3
    g = duel()
    before = list(g["ids"])
    g["phase"] = "results"
    vocab.apply_action(g, "Alice", "rematch", 2000)
    vocab.apply_action(g, "Bob", "rematch", 2001)
    assert not set(before) & set(g["ids"])


def test_context_distractors_vary_by_room_but_not_by_request():
    w = BY_SLUG["discerning"]
    assert w.alternatives == ()
    seen = set()
    for code in ("AAA111", "BBB222", "CCC333", "DDD444", "EEE555", "FFF666"):
        g = vocab.make_game(code, "duel", player("Alice"), now=1000)
        g["ids"] = [w.id] * vocab.ROUNDS
        q = vocab.full_question(g, 1)
        assert q == vocab.full_question(g, 1)
        assert len(q["options"]) == 3 and w.word in q["options"]
        seen.add(frozenset(q["options"]))
    assert len(seen) > 1


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


def test_guest_player_id_is_stable_and_never_holds_the_token():
    token = "t" * 43
    gid = vocab.guest_player_id(token)
    assert gid == vocab.guest_player_id(token)
    assert gid.startswith(vocab.GUEST_PREFIX)
    assert token not in gid
    assert gid != vocab.guest_player_id("u" * 43)


def test_claim_hands_the_seat_over_with_its_answers():
    guest = vocab.guest_player_id("t" * 43)
    g = vocab.make_game(
        "ABC123", "solo", vocab.new_player(guest, vocab.GUEST_NAME, 0), 0
    )
    answer(g, guest, vocab.full_question(g)["correct"], now=1000)
    vocab.claim(g, guest, "7", "ada", now=2000)
    me = g["players"][0]
    assert (me["id"], me["name"], me["seen"]) == ("7", "ada", 2000)
    assert me["answers"]["0"]["correct"] is True
    assert vocab.visible(g, "7")["history"][0]["answers"][0]["name"] == "ada"
    with pytest.raises(vocab.VocabError) as refused:
        vocab.claim(g, guest, "8", "bob", now=3000)
    assert refused.value.status == 403
