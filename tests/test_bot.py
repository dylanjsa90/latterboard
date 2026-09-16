"""The computer opponent: its move engine, and playing real matches against it."""

import random
import statistics

import pytest
import redis as sync_redis

from app.api.routes.matchmaking import (
    QUEUE_PREFIX,
    WAITING_SINCE_PREFIX,
    waiting_since_key,
)
from app.core.config import settings
from app.game import bot, wordle
from app.game.puzzles import cipher_feedback

# --- the move engine ----------------------------------------------------------
#
# Pure, seeded, and needs no database — so these run the bot through whole games
# and assert on how it actually plays, not just that it returns something.


def _play_word(answer: str, *, skill: float, rng: random.Random, budget: int = 6):
    """Play a solo word game to the end; returns the winning guess number or None."""
    history: bot.WordHistory = []
    for attempt in range(1, budget + 1):
        guess = bot.choose_word(history, skill=skill, rng=rng)
        grades = wordle.evaluate_guess(answer, guess)
        history.append((guess, grades))
        if all(grade == "correct" for grade in grades):
            return attempt
    return None


def _play_cipher(answer: list[int], *, skill: float, rng: random.Random, budget: int = 8):
    history: bot.CipherHistory = []
    for attempt in range(1, budget + 1):
        guess = bot.choose_cipher(history, skill=skill, rng=rng)
        feedback = cipher_feedback(guess, answer)
        history.append((guess, feedback))
        if feedback["won"]:
            return attempt
    return None


def test_word_candidates_never_rule_out_the_answer():
    """The filter may only ever remove words that cannot be the answer."""
    rng = random.Random(3)
    for answer in ("crane", "fluff", "abbey", "sassy"):
        history: bot.WordHistory = []
        for _ in range(5):
            guess = bot.choose_word(history, rng=rng)
            history.append((guess, wordle.evaluate_guess(answer, guess)))
            assert answer in bot.word_candidates(history)


def test_cipher_candidates_never_rule_out_the_answer():
    rng = random.Random(3)
    for answer in ([0, 1, 2, 3], [5, 4, 3, 2], [2, 0, 5, 1]):
        history: bot.CipherHistory = []
        for _ in range(4):
            guess = bot.choose_cipher(history, rng=rng)
            history.append((guess, cipher_feedback(guess, answer)))
            assert answer in bot.cipher_candidates(history)


def test_word_guesses_are_always_playable():
    """Every guess must pass the same validity check the route applies."""
    rng = random.Random(5)
    history: bot.WordHistory = []
    for _ in range(6):
        guess = bot.choose_word(history, rng=rng)
        assert wordle.is_valid_word(guess)
        history.append((guess, wordle.evaluate_guess("pluck", guess)))


def test_cipher_guesses_are_always_well_formed():
    """4 digits in range. Distinct too: the answer always is, so a repeat wastes
    an attempt on a code that cannot win."""
    rng = random.Random(5)
    history: bot.CipherHistory = []
    for _ in range(8):
        guess = bot.choose_cipher(history, rng=rng)
        assert len(guess) == bot.CIPHER_LENGTH
        assert all(0 <= digit < bot.CIPHER_DIGIT_RANGE for digit in guess)
        assert len(set(guess)) == bot.CIPHER_LENGTH
        history.append((guess, cipher_feedback(guess, [1, 3, 0, 5])))


def test_a_perfect_bot_never_wastes_a_guess():
    """At skill 1.0 every guess must still be a possible answer."""
    rng = random.Random(9)
    history: bot.WordHistory = []
    for _ in range(5):
        guess = bot.choose_word(history, skill=1.0, rng=rng)
        assert guess in bot.word_candidates(history)
        history.append((guess, wordle.evaluate_guess("blimp", guess)))


def test_a_careless_bot_sometimes_wastes_a_guess():
    """The counterpart: low skill must actually produce ruled-out guesses, or the
    handicap isn't doing anything."""
    rng = random.Random(1)
    wasted = 0
    for _ in range(60):
        history: bot.WordHistory = [("crane", wordle.evaluate_guess("blimp", "crane"))]
        guess = bot.choose_word(history, skill=0.0, rng=rng)
        if guess not in bot.word_candidates(history):
            wasted += 1
    assert wasted > 0


def test_word_play_lands_in_a_human_band():
    """Ranges, not exact figures, so tuning `skill` doesn't break the suite.

    The point of these numbers is that the bot is beatable: needing ~3.7 guesses
    means it wins well under half of turn-based matches, where it gets about 3.
    """
    rng = random.Random(7)
    answers = random.Random(11)
    solved = [
        result
        for _ in range(60)
        if (result := _play_word(answers.choice(bot._WORDS), skill=bot.DEFAULT_SKILL, rng=rng))
    ]
    assert len(solved) >= 54  # solves at least 90% of the time within 6
    assert 3.0 <= statistics.mean(solved) <= 4.6


def test_cipher_play_lands_in_a_human_band():
    rng = random.Random(7)
    answers = random.Random(11)
    solved = [
        result
        for _ in range(60)
        if (
            result := _play_cipher(
                list(answers.choice(bot._CIPHER_CODES)), skill=bot.DEFAULT_SKILL, rng=rng
            )
        )
    ]
    assert len(solved) == 60  # 360 codes and 8 attempts: it should always get there
    assert 3.5 <= statistics.mean(solved) <= 5.5


def test_skill_ordering_holds():
    """A more skilled bot solves in fewer guesses on average. If this inverts, the
    `skill` knob is wired backwards."""

    def mean_for(skill: float) -> float:
        rng = random.Random(7)
        answers = random.Random(11)
        solved = [
            result
            for _ in range(80)
            if (result := _play_word(answers.choice(bot._WORDS), skill=skill, rng=rng))
        ]
        return statistics.mean(solved)

    assert mean_for(1.0) < mean_for(0.4)


def test_the_same_seed_replays_the_same_game():
    """Seeded reproducibility is what makes the rest of these tests meaningful."""
    first = _play_word("crane", skill=bot.DEFAULT_SKILL, rng=random.Random(42))
    second = _play_word("crane", skill=bot.DEFAULT_SKILL, rng=random.Random(42))
    assert first == second


# --- playing real matches against the computer --------------------------------

MATCHMAKING = "/api/v1/matchmaking"
MATCHES = "/api/v1/matches"
BOT = settings.BOT_USER_USERNAME


@pytest.fixture(autouse=True)
def empty_queues():
    """Tests share Redis and recreate users with the same ids, so leftover queue or
    waiting-since state would leak between them."""
    redis_client = sync_redis.Redis.from_url(settings.REDIS_URL)
    for prefix in (QUEUE_PREFIX, WAITING_SINCE_PREFIX):
        for key in redis_client.scan_iter(match=f"{prefix}:*"):
            redis_client.delete(key)
    yield redis_client
    redis_client.close()


@pytest.fixture
def instant_bot(monkeypatch):
    """Hand out the computer on the first call, so gameplay tests don't wait."""
    monkeypatch.setattr(settings, "MATCHMAKING_BOT_WAIT_SECONDS", 0)


def _start_against_bot(client, headers, game):
    body = client.post(f"{MATCHMAKING}/{game}", headers=headers).json()
    assert body["status"] == "matched", body
    return body["match"]


def test_a_fresh_player_waits_before_being_offered_the_computer(client, inviter_headers):
    body = client.post(f"{MATCHMAKING}/wordle", headers=inviter_headers).json()
    assert body["status"] == "queued"


def test_the_computer_arrives_once_the_player_has_waited(
    client, inviter_headers, empty_queues
):
    assert client.post(f"{MATCHMAKING}/wordle", headers=inviter_headers).json()["status"] == (
        "queued"
    )
    # Backdate the first-join time rather than sleeping, so this exercises the real
    # comparison against MATCHMAKING_BOT_WAIT_SECONDS.
    key = waiting_since_key("wordle")
    user_id = next(iter(empty_queues.hkeys(key))).decode()
    waited = float(empty_queues.hget(key, user_id))
    empty_queues.hset(key, user_id, waited - settings.MATCHMAKING_BOT_WAIT_SECONDS - 1)

    match = _start_against_bot(client, inviter_headers, "wordle")
    assert match["invitee_username"] == BOT
    assert match["invitee_is_bot"] is True
    assert match["inviter_is_bot"] is False
    # The player waited, so they invite and take the first turn.
    assert match["current_turn_username"] == "inviter"


def test_re_queueing_does_not_reset_the_wait(client, inviter_headers, empty_queues):
    """The sorted set's score is refreshed on every re-join; the wait must not be, or
    a polling client would never reach the computer."""
    client.post(f"{MATCHMAKING}/wordle", headers=inviter_headers)
    key = waiting_since_key("wordle")
    first = dict(empty_queues.hgetall(key))
    client.post(f"{MATCHMAKING}/wordle", headers=inviter_headers)
    assert dict(empty_queues.hgetall(key)) == first


def test_switching_games_starts_the_wait_over(client, inviter_headers, empty_queues):
    client.post(f"{MATCHMAKING}/wordle", headers=inviter_headers)
    client.post(f"{MATCHMAKING}/word_race", headers=inviter_headers)
    assert empty_queues.hgetall(waiting_since_key("wordle")) == {}


@pytest.mark.usefixtures("instant_bot")
def test_co_op_never_pairs_with_the_computer(client, inviter_headers):
    """Sudoku co-op is cooperative and free-form, so there is no opponent to stand in."""
    body = client.post(f"{MATCHMAKING}/sudoku_coop", headers=inviter_headers).json()
    assert body["status"] == "queued"


@pytest.mark.usefixtures("instant_bot")
def test_the_computer_can_be_turned_off(client, inviter_headers, monkeypatch):
    monkeypatch.setattr(settings, "BOT_OPPONENT_ENABLED", False)
    body = client.post(f"{MATCHMAKING}/wordle", headers=inviter_headers).json()
    assert body["status"] == "queued"


def test_the_computer_cannot_be_invited_directly(client, inviter_headers):
    """Nothing would ever accept it, so the match would hang for ever."""
    r = client.post(
        f"{MATCHES}/invite", json={"opponent_username": BOT}, headers=inviter_headers
    )
    assert r.status_code == 400


# --- gameplay -----------------------------------------------------------------

WORDS = ["crane", "stoic", "blimp", "pluck", "vodka", "jumpy", "ghost", "wedge"]


def _detail(client, headers, match_id):
    r = client.get(f"{MATCHES}/{match_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _play_out(client, headers, match, path, moves):
    """Take turns until the match ends or the player runs out of moves."""
    for move in moves:
        detail = _detail(client, headers, match["id"])
        if detail["status"] != "in_progress":
            return detail
        if match["game"] == "wordle" and detail["current_turn_username"] != "inviter":
            return detail
        r = client.post(f"{MATCHES}/{match['id']}/{path}", json=move, headers=headers)
        if r.status_code == 409:  # our side of the race is done; nothing left to do
            break
        assert r.status_code == 200, r.text
    return _detail(client, headers, match["id"])


@pytest.mark.usefixtures("instant_bot")
def test_the_computer_replies_before_the_guess_returns(client, inviter_headers):
    """The bot moves in a background task, which runs after the response is built but
    before the request completes — so its guess is already recorded on arrival."""
    match = _start_against_bot(client, inviter_headers, "wordle")

    r = client.post(
        f"{MATCHES}/{match['id']}/guess", json={"word": "crane"}, headers=inviter_headers
    )
    assert r.status_code == 200
    # Built before the bot moved, so it still hands the turn over.
    assert r.json()["current_turn_username"] == BOT

    detail = _detail(client, inviter_headers, match["id"])
    if detail["status"] == "in_progress":
        assert detail["current_turn_username"] == "inviter"
    # Two guesses on the shared board: the player's, then the computer's.
    assert len(detail["guesses"]) == 2
    assert detail["guesses"][1]["username"] == BOT
    assert wordle.is_valid_word(detail["guesses"][1]["word"])


@pytest.mark.usefixtures("instant_bot")
def test_a_wordle_match_against_the_computer_finishes(client, inviter_headers):
    match = _start_against_bot(client, inviter_headers, "wordle")
    detail = _play_out(
        client, inviter_headers, match, "guess", [{"word": w} for w in WORDS]
    )
    assert detail["status"] == "completed"
    # 6 guesses are shared between the two players, so neither gets more than its half.
    assert len(detail["guesses"]) <= match["max_guesses"]
    assert detail["winner_username"] in (None, "inviter", BOT)


@pytest.mark.usefixtures("instant_bot")
def test_a_word_race_against_the_computer_finishes(client, inviter_headers):
    match = _start_against_bot(client, inviter_headers, "word_race")
    detail = _play_out(
        client, inviter_headers, match, "word/guess", [{"word": w} for w in WORDS]
    )
    assert detail["status"] == "completed"
    assert detail["race"]["answer"] is not None  # revealed once the race is over


@pytest.mark.usefixtures("instant_bot")
def test_a_cipher_race_against_the_computer_finishes(client, inviter_headers):
    match = _start_against_bot(client, inviter_headers, "cipher_race")
    attempts = [
        {"attempt": list(code)}
        for code in ([0, 1, 2, 3], [1, 0, 3, 2], [2, 3, 0, 1], [3, 2, 1, 0],
                     [4, 5, 0, 1], [5, 4, 1, 0], [0, 2, 4, 5], [1, 3, 5, 4])
    ]
    detail = _play_out(client, inviter_headers, match, "cipher/guess", attempts)
    assert detail["status"] == "completed"


@pytest.mark.usefixtures("instant_bot")
def test_the_computer_keeps_racing_after_the_player_finishes(client, inviter_headers):
    """A race can't be decided while the bot's side is unsettled, and nothing else
    would prompt it once the player has stopped guessing."""
    match = _start_against_bot(client, inviter_headers, "word_race")
    detail = _play_out(
        client, inviter_headers, match, "word/guess", [{"word": w} for w in WORDS]
    )
    assert detail["status"] == "completed"
    opponent = detail["race"]["opponent"]
    assert opponent["finished"] is True


@pytest.mark.usefixtures("instant_bot")
def test_solo_matches_stay_off_the_record_and_the_leaderboard(client, inviter_headers):
    match = _start_against_bot(client, inviter_headers, "wordle")
    _play_out(client, inviter_headers, match, "guess", [{"word": w} for w in WORDS])

    history = client.get(f"{MATCHES}/me/history", headers=inviter_headers).json()
    # The player still sees the game they played...
    played = [item for item in history["items"] if item["id"] == match["id"]]
    assert len(played) == 1
    assert played[0]["opponent_is_bot"] is True
    # ...but it neither pads nor dents their record.
    assert history["record"] == {"won": 0, "lost": 0, "drawn": 0}

    # And no score rows exist, so neither player can reach the leaderboard.
    mine = client.get("/api/v1/scores/me/wordle_vs", headers=inviter_headers).json()
    assert mine == []
    board = client.get("/api/v1/scores/leaderboard/wordle_vs/all-time").json()
    assert [entry["username"] for entry in board] == []


@pytest.mark.usefixtures("instant_bot")
def test_the_active_rail_flags_the_computer(client, inviter_headers):
    match = _start_against_bot(client, inviter_headers, "wordle")
    active = client.get(f"{MATCHES}/me/active", headers=inviter_headers).json()
    mine = [item for item in active if item["id"] == match["id"]]
    assert len(mine) == 1
    assert mine[0]["opponent_is_bot"] is True
    assert mine[0]["opponent_username"] == BOT


@pytest.mark.usefixtures("instant_bot")
def test_the_computers_move_reaches_the_websocket(client, inviter_headers):
    """Covers the whole delivery path, including that a background task's broadcast
    survives the BaseHTTPMiddleware wrapping every response."""
    match = _start_against_bot(client, inviter_headers, "wordle")
    with client.websocket_connect("/ws/wordle", headers=inviter_headers) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "join_match", "match_id": match["id"]})
        assert ws.receive_json()["type"] == "joined_match"

        client.post(
            f"{MATCHES}/{match['id']}/guess", json={"word": "crane"}, headers=inviter_headers
        )

        # Each move sends an `opponent_guessed` then a `your_turn`, so a move and
        # its reply are four frames — unless a guess ends the match first.
        frames = []
        while len(frames) < 4:
            frames.append(ws.receive_json())
            if frames[-1]["type"] == "match_completed":
                break

    kinds = [frame["type"] for frame in frames]
    assert kinds[0] == "opponent_guessed"
    assert frames[0]["username"] == "inviter"
    if kinds[-1] != "match_completed":
        assert kinds == ["opponent_guessed", "your_turn", "opponent_guessed", "your_turn"]
        # The turn goes to the computer and comes straight back, with no client
        # action in between.
        assert frames[1]["current_turn_username"] == BOT
        assert frames[2]["username"] == BOT
        assert frames[3]["current_turn_username"] == "inviter"
