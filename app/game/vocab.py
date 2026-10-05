"""Pure rules for Vocab Challenger rooms, ported from vocab-app's server/engine.ts.

A room is a five-round solo session or a two-player duel. Phases change lazily: the
crud layer calls `advance` on every request, and clients poll often enough that a
round resolves within a poll of its deadline. Times are epoch milliseconds because
the client does its countdowns in ms against `server_now`.

No FastAPI/pydantic/SQLAlchemy imports here, mirroring app/game/match_modes.py.
"""

import random
import re
import time
from datetime import timedelta
from typing import Any, Literal, TypedDict

from app.game.vocab_words import WORDS, VocabWord

ROUNDS = 5
ROUND_MS = 40_000
FEEDBACK_MS = 16_000
ROOM_LIFETIME_MS = 24 * 60 * 60 * 1000
# Days until a saved word is due again, by review level; level 0 is a miss (10 minutes).
REVIEW_DAYS = (10 / 1440, 1, 3, 7, 14, 30, 60)

Mode = Literal["solo", "duel"]
Phase = Literal["lobby", "question", "feedback", "results"]


class VocabError(Exception):
    """A request the room's current state doesn't allow; `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class Answer(TypedDict):
    choice: int
    points: int
    correct: bool
    ms: int


class Player(TypedDict):
    id: str
    name: str
    ready: bool
    # Keyed by str(round): JSON object keys are strings.
    answers: dict[str, Answer]
    seen: int


class Game(TypedDict):
    code: str
    mode: Mode
    phase: Phase
    round: int
    deadline: int
    started: int
    ids: list[int]
    players: list[Player]
    created: int
    generation: int
    rematch_ready: list[str]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_player(player_id: str, name: str, now: int) -> Player:
    return {"id": player_id, "name": name, "ready": False, "answers": {}, "seen": now}


def _pick_words() -> list[int]:
    """Five distinct words, at least one from each difficulty, in random order."""
    selected: list[int] = []
    for difficulty in ("Foundation", "Intermediate", "Advanced"):
        pool = [w.id for w in WORDS if w.difficulty == difficulty]
        if pool:
            selected.append(random.choice(pool))
    for w in random.sample(WORDS, len(WORDS)):
        if len(selected) == ROUNDS:
            break
        if w.id not in selected:
            selected.append(w.id)
    return random.sample(selected, len(selected))


def make_game(code: str, mode: Mode, player: Player, now: int) -> Game:
    return {
        "code": code,
        "mode": mode,
        "phase": "question" if mode == "solo" else "lobby",
        "round": 0,
        "deadline": now + ROUND_MS,
        "started": now,
        "ids": _pick_words(),
        "players": [player],
        "created": now,
        "generation": 0,
        "rematch_ready": [],
    }


def expired(g: Game, now: int) -> bool:
    return now - g["created"] > ROOM_LIFETIME_MS


def _order(g: Game, r: int, length: int) -> list[int]:
    """A permutation of option indexes that stays the same on every request for this
    round, so a reconnecting player sees the same order; the key stays server-side."""
    rng = random.Random(f"{g['code']}:{g['created']}:{g['generation']}:{r}")
    return rng.sample(range(length), length)


def _with_blank(sentence: str, word: str) -> str:
    return re.sub(
        rf"\b{re.escape(word)}\b", "_____", sentence, count=1, flags=re.IGNORECASE
    )


def _base(w: VocabWord) -> dict[str, Any]:
    return {
        "id": w.id,
        "word": w.word,
        "pos": w.pos,
        "definition": w.definition,
        "difficulty": w.difficulty,
        "example": w.example,
        "synonyms": w.synonyms,
        "nuance": w.nuance,
    }


def full_question(g: Game, r: int | None = None) -> dict[str, Any]:
    """Round `r`'s question with its key and explanations. Even rounds ask which sentence
    uses the word correctly; odd rounds ask which word fills a blank in a sentence."""
    r = g["round"] if r is None else r
    w = WORDS[g["ids"][r]]
    if r % 2 == 0:
        order = _order(g, r, len(w.options))
        return {
            **_base(w),
            "kind": "usage",
            "prompt": w.prompt,
            "options": [w.options[i] for i in order],
            "reasons": [w.reasons[i] for i in order],
            "correct": order.index(0),
        }
    by_word = {x.word: x for x in WORDS}
    distractors = [by_word[a] for a in w.alternatives if a in by_word]
    for candidate in WORDS:
        if len(distractors) >= 2:
            break
        if (
            candidate.pos == w.pos
            and candidate.id != w.id
            and candidate not in distractors
        ):
            distractors.append(candidate)
    words = [w, *distractors[:2]]
    order = _order(g, r, len(words))
    return {
        **_base(w),
        "kind": "context",
        "prompt": _with_blank(w.options[0], w.word),
        "options": [words[i].word for i in order],
        "reasons": [
            w.reasons[0]
            if i == 0
            else f"{words[i].word} means “{words[i].definition}” "
            "That does not express the intended distinction here."
            for i in order
        ],
        "correct": order.index(0),
    }


def next_round(g: Game, now: int) -> None:
    if g["round"] == ROUNDS - 1:
        g["phase"] = "results"
        return
    g["round"] += 1
    g["phase"] = "question"
    g["started"] = now
    g["deadline"] = now + ROUND_MS


def advance(g: Game, now: int) -> None:
    """Apply whatever transition time (or everyone answering) has made due."""
    key = str(g["round"])
    if g["phase"] == "question" and (
        now >= g["deadline"] or all(key in p["answers"] for p in g["players"])
    ):
        g["phase"] = "feedback"
        # Solo feedback waits for the player to press next.
        g["deadline"] = now + (ROOM_LIFETIME_MS if g["mode"] == "solo" else FEEDBACK_MS)
    elif g["phase"] == "feedback" and g["mode"] == "duel" and now >= g["deadline"]:
        next_round(g, now)


def participant(g: Game, player_id: str) -> Player:
    for p in g["players"]:
        if p["id"] == player_id:
            return p
    raise VocabError("You are not a participant in this room.", 403)


def join(g: Game, player: Player, now: int) -> None:
    """Add `player` to a duel's lobby, or just mark them seen if they're already in it."""
    if g["mode"] != "duel":
        raise VocabError("This is a solo session.", 403)
    if any(p["id"] == player["id"] for p in g["players"]):
        participant(g, player["id"])["seen"] = now
        advance(g, now)
        return
    if g["phase"] != "lobby" or len(g["players"]) >= 2:
        raise VocabError("This room is full or the match has started.", 409)
    g["players"].append(player)


def touch(g: Game, player_id: str, now: int) -> None:
    """Record that a participant is still polling, and catch up on due transitions."""
    participant(g, player_id)["seen"] = now
    advance(g, now)


def apply_action(
    g: Game,
    player_id: str,
    action: str,
    now: int,
    *,
    choice: int | None = None,
    round_index: int | None = None,
    generation: int | None = None,
) -> None:
    p = participant(g, player_id)
    p["seen"] = now
    advance(g, now)
    if action == "ready":
        if g["phase"] != "lobby":
            raise VocabError("The match has already started.", 409)
        p["ready"] = not p["ready"]
        if len(g["players"]) == 2 and all(x["ready"] for x in g["players"]):
            g["phase"] = "question"
            g["started"] = now
            g["deadline"] = now + ROUND_MS
    elif action == "answer":
        if (
            g["phase"] != "question"
            or g["round"] != round_index
            or g["generation"] != generation
            or now >= g["deadline"]
        ):
            raise VocabError("This round has ended. Your game will refresh.", 409)
        key = str(g["round"])
        if key in p["answers"]:
            raise VocabError("Your answer is already locked in.", 409)
        q = full_question(g)
        if choice is None or not 0 <= choice < len(q["options"]):
            raise VocabError("Choose one of the available answers.")
        correct = choice == q["correct"]
        ms = max(0, now - g["started"])
        p["answers"][key] = {
            "choice": choice,
            "correct": correct,
            "ms": ms,
            # Accuracy first: speed adds at most 5 points.
            "points": 100 + max(0, 5 - ms // 8000) if correct else 0,
        }
        advance(g, now)
    elif action == "next":
        if g["mode"] != "solo" or g["phase"] != "feedback":
            raise VocabError("Wait for this round to finish.", 409)
        next_round(g, now)
    elif action == "rematch":
        if g["phase"] != "results":
            raise VocabError("Finish this match first.", 409)
        if player_id not in g["rematch_ready"]:
            g["rematch_ready"].append(player_id)
        if all(x["id"] in g["rematch_ready"] for x in g["players"]):
            g["ids"] = _pick_words()
            g["round"] = 0
            g["generation"] += 1
            g["rematch_ready"] = []
            for x in g["players"]:
                x["ready"] = False
                x["answers"] = {}
            g["phase"] = "question" if g["mode"] == "solo" else "lobby"
            g["started"] = now
            g["deadline"] = now + ROUND_MS
    else:
        raise VocabError("Unknown game action.")


_PRIVATE = ("correct", "reasons", "example", "synonyms", "nuance")


def visible(g: Game, player_id: str) -> dict[str, Any]:
    """The room as `player_id` may see it: keys, explanations, and the opponent's choices
    only once the round has resolved."""
    me = participant(g, player_id)
    revealed = g["phase"] in ("feedback", "results")
    q = full_question(g)
    question = q if revealed else {k: v for k, v in q.items() if k not in _PRIVATE}
    if not revealed and q["kind"] == "context":
        # The word is the answer to a context question.
        question = {
            **question,
            "id": None,
            "word": None,
            "definition": None,
            "pos": None,
        }
    shown = ROUNDS if g["phase"] == "results" else g["round"] + (1 if revealed else 0)
    history = [
        {
            "question": full_question(g, r),
            "answers": [
                {"name": p["name"], **p["answers"].get(str(r), {})}
                for p in g["players"]
            ],
        }
        for r in range(shown)
    ]
    mine = me["answers"].get(str(g["round"]))
    return {
        "code": g["code"],
        "mode": g["mode"],
        "phase": g["phase"],
        "round": g["round"],
        "generation": g["generation"],
        "deadline": g["deadline"],
        "rematch_requested": player_id in g["rematch_ready"],
        "players": [
            {
                "id": "me" if p["id"] == player_id else "opponent",
                "name": p["name"],
                "ready": p["ready"],
                "seen": p["seen"],
                "answered": str(g["round"]) in p["answers"],
                "score": sum(
                    a["points"]
                    for r, a in p["answers"].items()
                    if int(r) < g["round"] or revealed
                ),
            }
            for p in g["players"]
        ],
        "question": question,
        "mine": mine["choice"] if mine else None,
        "history": history,
    }


def next_review(level: int, known: bool) -> tuple[int, timedelta]:
    """A saved word's new level after a recall check, and how long until it's due again."""
    new_level = min(level + 1, len(REVIEW_DAYS) - 1) if known else 0
    return new_level, timedelta(days=REVIEW_DAYS[new_level])
