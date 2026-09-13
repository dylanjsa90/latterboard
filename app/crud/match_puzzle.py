"""Moves and per-player views for race and co-op matches, whose private puzzle and
live state live in MatchPuzzle. The rules themselves are in app/game/match_modes.py.
"""

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.crud.game_score import game_score as crud_game_score
from app.game import match_modes
from app.models.match import Match, MatchPuzzle
from app.models.user import User
from app.schemas.game_score import GameScoreCreate
from app.schemas.match import (
    RaceDetail,
    RaceGuessOut,
    RaceGuessResult,
    RacePlayer,
    SudokuCoopDetail,
    SudokuCoopMoveResult,
)
from app.utils import utcnow

T = TypeVar("T")

# How many times to replay a move that keeps losing out to the other player's.
MAX_APPLY_ATTEMPTS = 3


class MoveRejected(Exception):
    """The move isn't allowed in the match's current state; the message says why."""


def _solved(guesses: list[dict[str, Any]]) -> bool:
    return bool(guesses and guesses[-1]["correct"])


def _finished(guesses: list[dict[str, Any]], max_attempts: int) -> bool:
    return _solved(guesses) or len(guesses) >= max_attempts


def _answer(row: MatchPuzzle) -> str | list[int]:
    answer: str | list[int] = row.data["answer"] if "answer" in row.data else row.data["digits"]
    return answer


def _username(db: Session, user_id: int | None) -> str | None:
    user = db.get(User, user_id) if user_id is not None else None
    return user.username if user else None


class CRUDMatchPuzzle:
    def build(self, match: Match) -> MatchPuzzle:
        """A fresh puzzle row for a new race or co-op match. The match must already be
        flushed so it has an id."""
        data = match_modes.new_puzzle(match.game)
        players = (match.inviter_id, match.invitee_id)
        return MatchPuzzle(
            match_id=match.id,
            data=data,
            state=match_modes.initial_state(match.game, players, data),
        )

    def get_by_match(self, db: Session, match_id: int) -> MatchPuzzle:
        return db.query(MatchPuzzle).filter(MatchPuzzle.match_id == match_id).one()

    def _apply(self, db: Session, match: Match, change: Callable[[MatchPuzzle], T]) -> T:
        """Run `change` against the match's puzzle row and commit.

        Both players can move at once, so `change` must re-check everything it relies
        on (match status included) from what it's given. If the other player's move
        commits first, MatchPuzzle.version turns our write into a StaleDataError, and
        we replay the move on the fresh state instead of overwriting theirs.
        """
        for _ in range(MAX_APPLY_ATTEMPTS):
            result = change(self.get_by_match(db, match.id))
            try:
                db.commit()
            except StaleDataError:
                db.rollback()
                continue
            return result
        raise MoveRejected("The board changed while saving your move; try again")

    def _complete(self, match: Match, winner_id: int | None) -> None:
        match.status = "completed"
        match.winner_id = winner_id
        match.completed_at = utcnow()

    # --- races ------------------------------------------------------------

    def submit_race_guess(
        self, db: Session, match: Match, user: User, guess: str | list[int]
    ) -> RaceGuessResult:
        def change(row: MatchPuzzle) -> tuple[int, Any, bool]:
            if match.status != "in_progress":
                raise MoveRejected("Match is not in progress")
            guesses = row.state["guesses"]
            mine = guesses[str(user.id)]
            if _finished(mine, match.max_guesses):
                raise MoveRejected("You've finished this race; waiting on your opponent")

            result, correct = match_modes.grade_race_guess(row.data, guess)
            guesses = {
                **guesses,
                str(user.id): [*mine, {"guess": guess, "result": result, "correct": correct}],
            }
            # Reassign rather than mutate: JSON columns don't track in-place changes.
            row.state = {**row.state, "guesses": guesses}

            decided, winner_id = match_modes.race_outcome(
                {int(pid): (len(g), _solved(g)) for pid, g in guesses.items()},
                match.max_guesses,
            )
            if decided:
                self._complete(match, winner_id)
            return len(mine) + 1, result, correct

        turn_number, result, correct = self._apply(db, match, change)
        row = self.get_by_match(db, match.id)
        over = match.status == "completed"
        if over:
            self._record_race_scores(db, match, row)
        return RaceGuessResult(
            match_id=match.id,
            turn_number=turn_number,
            guess=guess,
            result=result,
            correct=correct,
            status=match.status,
            winner_username=_username(db, match.winner_id),
            answer=_answer(row) if over else None,
        )

    def _record_race_scores(self, db: Session, match: Match, row: MatchPuzzle) -> None:
        # The winner, or both players on a tied solve, score; everyone else gets 0.
        guesses_by_player = row.state["guesses"]
        for pid, guesses in guesses_by_player.items():
            scored = _solved(guesses) and match.winner_id in (None, int(pid))
            score = match_modes.race_score(len(guesses), match.max_guesses) if scored else 0
            crud_game_score.create_score(
                db, int(pid), GameScoreCreate(game=match.game, score=score)
            )

    def race_detail(self, db: Session, match: Match, viewer_id: int) -> RaceDetail:
        row = self.get_by_match(db, match.id)
        over = match.status == "completed"
        opponent_id = match.invitee_id if viewer_id == match.inviter_id else match.inviter_id

        def player(user_id: int, show_guesses: bool) -> RacePlayer:
            guesses = row.state["guesses"][str(user_id)]
            return RacePlayer(
                username=_username(db, user_id) or "",
                guesses=[
                    RaceGuessOut(
                        guess=g["guess"] if show_guesses else None,
                        result=g["result"],
                        correct=g["correct"],
                    )
                    for g in guesses
                ],
                solved=_solved(guesses),
                finished=_finished(guesses, match.max_guesses),
            )

        # The opponent's grades are visible throughout; their letters/digits only
        # once the race is over.
        return RaceDetail(
            you=player(viewer_id, True),
            opponent=player(opponent_id, over),
            answer=_answer(row) if over else None,
        )

    # --- sudoku co-op -----------------------------------------------------

    def submit_sudoku_move(
        self, db: Session, match: Match, index: int, value: int
    ) -> SudokuCoopMoveResult:
        def change(row: MatchPuzzle) -> bool:
            if match.status != "in_progress":
                raise MoveRejected("Match is not in progress")
            board = list(row.state["board"])
            if board[index] != 0:
                raise MoveRejected("That cell is already filled")

            solution = row.data["solution"]
            mistakes = row.state["mistakes"]
            correct: bool = solution[index] == value
            if correct:
                board[index] = value
            else:
                mistakes += 1
            outcome = (
                "solved"
                if board == solution
                else "failed"
                if mistakes >= match.max_guesses
                else None
            )
            row.state = {"board": board, "mistakes": mistakes, "outcome": outcome}
            if outcome is not None:
                self._complete(match, None)
            return correct

        correct = self._apply(db, match, change)
        state = self.get_by_match(db, match.id).state
        if match.status == "completed":
            self._record_sudoku_scores(db, match, state)
        return SudokuCoopMoveResult(
            match_id=match.id,
            index=index,
            value=value,
            correct=correct,
            mistakes=state["mistakes"],
            status=match.status,
            outcome=state["outcome"],
        )

    def _record_sudoku_scores(self, db: Session, match: Match, state: dict[str, Any]) -> None:
        score = (
            match_modes.sudoku_coop_score(state["mistakes"], match.max_guesses)
            if state["outcome"] == "solved"
            else 0
        )
        for user_id in (match.inviter_id, match.invitee_id):
            crud_game_score.create_score(db, user_id, GameScoreCreate(game=match.game, score=score))

    def sudoku_detail(self, db: Session, match: Match) -> SudokuCoopDetail:
        row = self.get_by_match(db, match.id)
        over = match.status == "completed"
        return SudokuCoopDetail(
            puzzle=row.data["puzzle"],
            board=row.state["board"],
            mistakes=row.state["mistakes"],
            max_mistakes=match.max_guesses,
            outcome=row.state["outcome"],
            solution=row.data["solution"] if over else None,
        )


match_puzzle = CRUDMatchPuzzle()
