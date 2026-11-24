"""The exact prompts and structured-output schemas the word-list generator sends Claude.

Lengths and counts are not in the schemas: structured outputs don't support minLength,
maxItems or minimum, so app/game/vocab_validation.py enforces them instead.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.game.vocab_words import DIFFICULTIES, POS, Difficulty
from app.vocab_pipeline.store import Draft


def _object(**properties: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _enum(values: Iterable[str]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


_STRING = {"type": "string"}
OPTION = _object(sentence=_STRING, reason=_STRING)
QUESTION = _object(prompt=_STRING, options=_array(OPTION))

PROPOSE_SCHEMA = _object(
    words=_array(_object(word=_STRING, pos=_enum(POS), difficulty=_enum(DIFFICULTIES)))
)
DRAFT_SCHEMA = _object(
    word=_STRING,
    pos=_enum(POS),
    pronunciation=_STRING,
    definition=_STRING,
    difficulty=_enum(DIFFICULTIES),
    example=_STRING,
    synonyms=_array(_STRING),
    nuance=_STRING,
    alternatives=_array(_STRING),
    room=QUESTION,
    practice=QUESTION,
)

PROPOSE_SYSTEM = """\
You choose new headwords for Vocab Challenger, a vocabulary game for teenagers and adults preparing for the SAT or the GRE or simply reading more widely. A good headword is one an educated reader meets in serious journalism, essays, fiction and test passages; one a learner can plausibly misuse; and one that can be tested with sentences about everyday situations.

Avoid technical jargon, slang, slurs, archaic words, proper nouns, abbreviations and multi-word phrases. Never propose a word in the exclusion list, nor an inflected or derived form of one: if "reticent" is listed, do not propose "reticence" or "reticently".

Difficulty levels:
- Foundation: common in good journalism and school reading (pragmatic, mitigate, resilient).
- Intermediate: typical of SAT reading passages and college writing (equivocal, salient, tenuous).
- Advanced: GRE-level or literary (perspicacious, inchoate, obviate).

Mix parts of speech (mostly adjectives, verbs and nouns, some adverbs) and spread the words across meanings instead of proposing clusters of near-synonyms. Give each word's part of speech in the sense a learner most needs."""  # noqa: E501

DRAFT_EXAMPLE: dict[str, Any] = {
    "word": "discerning",
    "pos": "adjective",
    "pronunciation": "/dɪˈsɜːr.nɪŋ/",
    "definition": "Showing good judgment and an ability to notice subtle differences.",
    "difficulty": "Foundation",
    "example": "A discerning listener hears the tension beneath a polite reply.",
    "synonyms": ["perceptive", "judicious"],
    "nuance": "Discernment implies judgment, not merely sharp eyesight.",
    "alternatives": ["judicious", "indiscriminate"],
    "room": {
        "prompt": "An editor is choosing which essays to publish.",
        "options": [
            {
                "sentence": "The discerning editor distinguished an original argument from a merely fashionable one.",  # noqa: E501
                "reason": "Careful discrimination between quality and appearance shows good judgment.",  # noqa: E501
            },
            {
                "sentence": "The discerning editor accepted every essay without reading it.",
                "reason": "Accepting everything without examination shows no discrimination.",
            },
            {
                "sentence": "The discerning editor could not tell an argument from an anecdote.",
                "reason": "Failing to notice an important distinction is the opposite of discernment.",  # noqa: E501
            },
            {
                "sentence": "The discerning editor printed the essays in alphabetical order.",
                "reason": "Alphabetical organization says nothing about judgment.",
            },
        ],
    },
    "practice": {
        "prompt": "A shopper compares two coats that look almost identical.",
        "options": [
            {
                "sentence": "The discerning shopper noticed that one coat had double-stitched seams and chose it.",  # noqa: E501
                "reason": "Spotting a small difference in quality and acting on it is what discernment means.",  # noqa: E501
            },
            {
                "sentence": "The discerning shopper bought the first coat on the rack without glancing at the other.",  # noqa: E501
                "reason": "Choosing without comparing shows no judgment at all.",
            },
            {
                "sentence": "The discerning shopper picked whichever coat had the tag that was easiest to read.",  # noqa: E501
                "reason": "Noticing what is easy to see is not the same as judging quality.",
            },
            {
                "sentence": "The discerning shopper argued with the cashier about the store's closing time.",  # noqa: E501
                "reason": "An argument about opening hours has nothing to do with telling things apart.",  # noqa: E501
            },
        ],
    },
}

DRAFT_SYSTEM = (
    """\
You write dictionary-quality entries for Vocab Challenger, a vocabulary game for teenagers and adults preparing for the SAT or the GRE or simply reading more widely. Each entry teaches one word through a definition, a usage example, a note on nuance and two multiple-choice questions. A person reviews every entry before it ships, so accuracy matters more than flourish: if you are unsure a usage is standard, write a different sentence.

Fill in every field of the JSON schema:

- word: the headword as it is usually written, lowercase unless it is always capitalized.
- pos: the part of speech of the sense you teach: noun, verb, adjective or adverb. Teach the word's most common sense in educated writing.
- pronunciation: General American IPA between slashes, with a stress mark and syllable dots, like /ɪˈkwɪv.ə.kəl/.
- definition: one plain sentence of 20 to 120 characters that a fifteen-year-old would understand, starting with a capital letter and ending with a period. Never use the word or a form of it in its own definition.
- difficulty: Foundation (common in good journalism and school reading: pragmatic, mitigate, resilient), Intermediate (typical of SAT reading passages and college writing: equivocal, salient, tenuous) or Advanced (GRE-level or literary: perspicacious, inchoate, obviate). When the request names a target difficulty, use it unless it is clearly wrong for the word.
- example: one natural sentence of 20 to 140 characters that uses the word correctly, in a situation different from both questions.
- synonyms: two to four words or short phrases close in meaning, most useful first, each under 30 characters. Never the word itself.
- nuance: one sentence of 20 to 160 characters on how the word differs from its nearest neighbor or from a word it is often confused with.
- alternatives: up to three single words that learners confuse with this word or that make good wrong answers in a fill-in-the-blank: near-synonyms with a different shade, opposites, or look-alikes (for equivocal: ambivalent, unequivocal). Same part of speech where possible, lowercase, never the word itself.
- room: the question used in live matches. prompt is a one-sentence scenario of 20 to 140 characters. options holds exactly four sentences of 20 to 120 characters, each paired with a reason of 15 to 140 characters. Every sentence uses the word. The first sentence uses it correctly, fits the scenario, and contains the word exactly once and in exactly the form given as word, because the game also blanks it out for a fill-in-the-blank round. The other three misuse it in instructive ways: confusing it with a look-alike, reversing its meaning, or ignoring its connotation. Each wrong sentence must be clearly wrong to someone who knows the word, not merely weaker. Each reason says why its sentence works or fails without repeating the definition word for word.
- practice: a second question, shown on the word's public page next to the definition. It follows the same rules as room, with the correct sentence first, but it must use a different scenario and four entirely new sentences and reasons. Never reuse or closely paraphrase any part of the room question or the example: the public page must not reveal the answers to the live game.

Write in American English. Keep every sentence concrete and self-contained, and keep the content suitable for a classroom: no brand names, real living people, politics, religion or violence. Vary the subjects and sentence shapes across the options, and do not make the correct sentence the longest one or the only one with a specific detail.

Here is a complete entry in the expected shape:

"""  # noqa: E501
    + json.dumps(DRAFT_EXAMPLE, ensure_ascii=False, indent=2)
)


def propose_message(counts: Mapping[Difficulty, int], exclude: Sequence[str]) -> str:
    return (
        f"Propose exactly {counts['Foundation']} Foundation, "
        f"{counts['Intermediate']} Intermediate and {counts['Advanced']} Advanced "
        "words.\n\n"
        "Exclusion list (words already in the game or already drafted):\n"
        f"{', '.join(sorted(exclude))}"
    )


def draft_message(d: Draft) -> str:
    if d["source"] == "wanted":
        return (
            f'Write the entry for "{d["word"]}". '
            "Choose its part of speech and difficulty."
        )
    return (
        f'Write the entry for "{d["word"]}" ({d["pos"]}). '
        f"Target difficulty: {d['difficulty']}."
    )
