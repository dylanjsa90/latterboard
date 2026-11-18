from app.game.vocab_validation import entry_errors, list_errors
from app.game.vocab_words import WORDS, word_from_json
from tests.vocab_helpers import entry


def test_shipped_words_and_helper_entries_are_valid():
    assert list_errors(WORDS) == []
    assert entry_errors(word_from_json(entry(46, "alpha"))) == []


def test_entry_errors_name_each_problem():
    def problems(word_id=46, **overrides):
        return " | ".join(
            entry_errors(word_from_json(entry(word_id, "alpha", **overrides)))
        )

    base = entry(46, "alpha")
    room, practice = base["options"], base["practice"]
    cases = [
        (
            {"options": room[:3], "reasons": base["reasons"][:3]},
            "alpha: options: expected 4, got 3",
        ),
        ({"reasons": base["reasons"][:3]}, "reasons must line up with options"),
        (
            {"options": [room[0].replace("alpha", "alphas"), *room[1:]]},
            'options[0] must contain "alpha" exactly once',
        ),
        (
            {"options": [*room[:2], "alpha " + "x" * 115, room[3]]},
            "options[2]: 121 characters (max 120)",
        ),
        ({"options": [room[0], room[0], *room[2:]]}, "options repeat"),
        ({"synonyms": ["alpha", "first"]}, "synonyms include the word"),
        ({"synonyms": ["first"]}, "synonyms: expected 2-4"),
        ({"alternatives": ["alpha"]}, "alternatives include the word itself"),
        ({"alternatives": ["b", "c", "d", "e"]}, "alternatives: at most 3"),
        ({"practice": None}, "practice is required"),
        ({"practice": {**practice, "answer": 4}}, "practice.answer must be 0-3"),
        (
            {"practice": {**practice, "prompt": base["prompt"]}},
            "practice.prompt is too close to the room question",
        ),
        (
            {
                "practice": {
                    **practice,
                    "options": [
                        room[1].replace(".", " today."),
                        *practice["options"][1:],
                    ],
                }
            },
            "practice.options[0] is too close to the room question",
        ),
        ({"example": room[0]}, "example is too close to the room question"),
        (
            {"definition": " Leading space."},
            "definition has leading or trailing spaces",
        ),
        ({"pronunciation": "ˈæl.fə"}, "pronunciation must be between slashes"),
        ({"pos": "pronoun"}, "pos must be one of noun, verb, adjective, adverb"),
        (
            {"difficulty": "Expert"},
            "difficulty must be one of Foundation, Intermediate, Advanced",
        ),
        ({"slug": "Alpha"}, "slug must match"),
    ]
    for overrides, expected in cases:
        assert expected in problems(**overrides), expected
    legacy = {"options": room[:3], "reasons": base["reasons"][:3], "practice": None}
    assert problems(word_id=45, **legacy) == ""
    strict = entry_errors(word_from_json(entry(45, "alpha", **legacy)), strict=True)
    assert any("practice is required" in e for e in strict)


def test_list_errors_check_ids_slugs_words_and_alternatives():
    def errors(*entries):
        return " | ".join(list_errors([word_from_json(e) for e in entries]))

    assert errors(entry(0, "alpha"), entry(1, "beta", alternatives=["alpha"])) == ""
    assert "beta: alternative gamma is not a shipped word" in errors(
        entry(0, "alpha"), entry(1, "beta", alternatives=["gamma"])
    )
    assert "duplicate slug alpha" in errors(entry(0, "alpha"), entry(1, "alpha"))
    assert "duplicate word alpha" in errors(
        entry(0, "alpha"), entry(1, "alpha", slug="alpha-two")
    )
    assert "ids must run from 0" in errors(entry(0, "alpha"), entry(2, "beta"))
