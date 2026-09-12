import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api import deps
from app.crud.puzzle import puzzle as crud_puzzle
from app.crud.puzzle_attempt import puzzle_attempt as crud_puzzle_attempt
from app.game import puzzles as puzzle_logic
from app.models import Puzzle, User
from app.schemas.puzzle import (
    CipherAttemptCreate,
    CipherFeedback,
    CipherPuzzlePublic,
    MemoryPuzzlePublic,
    MemoryRevealCreate,
    MemoryRevealResult,
    PuzzleStats,
    SudokuHintCreate,
    SudokuHintResult,
    SudokuMoveCreate,
    SudokuMoveResult,
    SudokuPuzzlePublic,
    WordGuessCreate,
    WordGuessResult,
    WordHint,
    WordHintRequest,
    WordPuzzlePublic,
)

router = APIRouter(prefix="/puzzles", tags=["puzzles"])

WORD_MAX_ATTEMPTS = 6
CIPHER_MAX_ATTEMPTS = 8


def _get_or_generate_puzzle(db: Session, game: str, puzzle_id: str | None) -> Puzzle:
    target_date = puzzle_logic.puzzle_date(puzzle_id, game)
    # Future puzzles would leak upcoming answers (and let anyone mint rows).
    row = None
    if target_date <= puzzle_logic.today():
        row = crud_puzzle.get_or_generate(db, game, target_date)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {game} puzzle for {target_date.isoformat()}",
        )
    return row


# --- stats -------------------------------------------------------------


@router.get("/me/stats", response_model=PuzzleStats)
def my_puzzle_stats(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    return crud_puzzle_attempt.get_stats(db, current_user.id)


# --- word -------------------------------------------------------------


@router.get("/word", response_model=WordPuzzlePublic)
def get_word_puzzle(
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    # Today's word is reserved for signed-in players; anonymous visitors get yesterday's.
    puzzle_id = None
    if current_user is None:
        puzzle_id = f"word-{(puzzle_logic.today() - timedelta(days=1)).isoformat()}"
    row = _get_or_generate_puzzle(db, "word", puzzle_id)
    answer = row.data["answer"]
    return WordPuzzlePublic(
        puzzle_id=f"word-{row.date.isoformat()}",
        word_length=puzzle_logic.WORD_LENGTH,
        max_attempts=WORD_MAX_ATTEMPTS,
        initial_guess=puzzle_logic.WORD_STARTER,
        initial_grade=puzzle_logic.grade_word(puzzle_logic.WORD_STARTER, answer),
    )


@router.post("/word/hint", response_model=WordHint)
def word_hint(
    body: WordHintRequest,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    row = _get_or_generate_puzzle(db, "word", body.puzzle_id)
    answer = row.data["answer"]

    if current_user is not None:
        crud_puzzle_attempt.get_or_create(db, current_user.id, "word", body.puzzle_id)

    return WordHint(letter=answer[0])


@router.post("/word/guess", response_model=WordGuessResult)
def word_guess(
    body: WordGuessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    row = _get_or_generate_puzzle(db, "word", body.puzzle_id)
    answer = row.data["answer"]

    grades = puzzle_logic.grade_word(body.guess, answer)
    won = body.guess.casefold() == answer.casefold()
    lost = not won and body.attempt_count >= WORD_MAX_ATTEMPTS

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db,
            current_user.id,
            "word",
            body.puzzle_id,
            won=won,
            completed=won or lost,
        )

    return WordGuessResult(grades=grades, won=won, lost=lost, answer=answer if lost else None)


# --- sudoku -------------------------------------------------------------


@router.get("/sudoku", response_model=SudokuPuzzlePublic)
def get_sudoku_puzzle(db: Session = Depends(deps.get_db)):
    row = _get_or_generate_puzzle(db, "sudoku", None)
    return SudokuPuzzlePublic(puzzle_id=f"sudoku-{row.date.isoformat()}", puzzle=row.data["puzzle"])


@router.post("/sudoku/move", response_model=SudokuMoveResult)
def sudoku_move(
    body: SudokuMoveCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    row = _get_or_generate_puzzle(db, "sudoku", body.puzzle_id)
    solution = row.data["solution"]

    correct = solution[body.index] == body.value
    board = list(body.board)
    if correct:
        board[body.index] = body.value
    completed = correct and board == solution

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db,
            current_user.id,
            "sudoku",
            body.puzzle_id,
            won=completed,
            completed=completed,
        )

    return SudokuMoveResult(correct=correct, completed=completed)


@router.post("/sudoku/hint", response_model=SudokuHintResult)
def sudoku_hint(
    body: SudokuHintCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    row = _get_or_generate_puzzle(db, "sudoku", body.puzzle_id)
    solution = row.data["solution"]

    value = solution[body.index]
    board = list(body.board)
    board[body.index] = value
    completed = board == solution

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db,
            current_user.id,
            "sudoku",
            body.puzzle_id,
            won=completed,
            completed=completed,
        )

    return SudokuHintResult(value=value, completed=completed)


# --- memory -------------------------------------------------------------


@router.get("/memory", response_model=MemoryPuzzlePublic)
def get_memory_puzzle(db: Session = Depends(deps.get_db)):
    row = crud_puzzle.get_by_game_variant(db, "memory", 0)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No memory puzzle seeded")
    symbols = row.data["symbols"]
    return MemoryPuzzlePublic(puzzle_id=f"memory-{uuid.uuid4()}", size=len(symbols) * 2)


@router.post("/memory/reveal", response_model=MemoryRevealResult)
def memory_reveal(
    body: MemoryRevealCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    if not body.puzzle_id.startswith("memory-"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory puzzle id")
    row = crud_puzzle.get_by_game_variant(db, "memory", 0)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No memory puzzle seeded")

    deck = puzzle_logic.seeded_memory_deck(body.puzzle_id, row.data["symbols"])
    if body.index >= len(deck):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory card index")

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(db, current_user.id, "memory", body.puzzle_id)

    return MemoryRevealResult(symbol=deck[body.index])


# --- cipher -------------------------------------------------------------


@router.get("/cipher", response_model=CipherPuzzlePublic)
def get_cipher_puzzle(db: Session = Depends(deps.get_db)):
    row = _get_or_generate_puzzle(db, "cipher", None)
    answer = row.data["digits"]
    return CipherPuzzlePublic(
        puzzle_id=f"cipher-{row.date.isoformat()}",
        slots=len(answer),
        max_attempts=CIPHER_MAX_ATTEMPTS,
        initial_attempt=puzzle_logic.CIPHER_STARTER,
        initial_feedback=CipherFeedback(
            **puzzle_logic.cipher_feedback(puzzle_logic.CIPHER_STARTER, answer)
        ),
    )


@router.post("/cipher/attempt", response_model=CipherFeedback)
def cipher_attempt(
    body: CipherAttemptCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
):
    row = _get_or_generate_puzzle(db, "cipher", body.puzzle_id)
    answer = row.data["digits"]

    feedback = puzzle_logic.cipher_feedback(body.attempt, answer)

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db,
            current_user.id,
            "cipher",
            body.puzzle_id,
            won=feedback["won"],
            completed=feedback["won"],
        )

    return CipherFeedback(**feedback)
