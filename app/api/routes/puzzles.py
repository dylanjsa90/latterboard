from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api import deps
from app.crud.puzzle import puzzle as crud_puzzle
from app.crud.puzzle_attempt import puzzle_attempt as crud_puzzle_attempt
from app.game import puzzles as puzzle_logic
from app.game import wordle
from app.models import Puzzle, User
from app.models.puzzle import PuzzleAttempt
from app.schemas.puzzle import (
    CipherAttemptCreate,
    CipherAttemptPublic,
    CipherAttemptResult,
    CipherFeedback,
    CipherPuzzlePublic,
    MemoryPuzzlePublic,
    MemoryRevealCreate,
    MemoryRevealResult,
    PuzzleHistory,
    PuzzleStats,
    SudokuHintCreate,
    SudokuHintResult,
    SudokuMoveCreate,
    SudokuMoveResult,
    SudokuPuzzlePublic,
    WordGuessCreate,
    WordGuessPublic,
    WordGuessResult,
    WordHint,
    WordHintRequest,
    WordPuzzlePublic,
)

router = APIRouter(prefix="/puzzles", tags=["puzzles"])


def _move_day(game: str, puzzle_id: str) -> date:
    """The day a move's `{game}-YYYY-MM-DD` puzzle_id names."""
    day = puzzle_logic.dated_puzzle(puzzle_id, game)
    if day is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {game} puzzle id"
        )
    # Future puzzles would leak upcoming answers (and let anyone mint rows).
    if day > puzzle_logic.today():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {game} puzzle for {day.isoformat()}",
        )
    return day


def _puzzle(db: Session, game: str, day: date) -> Puzzle:
    row = crud_puzzle.get_or_generate(db, game, day)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {game} puzzle for {day.isoformat()}",
        )
    return row


def _move_puzzle(db: Session, game: str, puzzle_id: str) -> Puzzle:
    return _puzzle(db, game, _move_day(game, puzzle_id))


def _saved_attempt(
    db: Session, current_user: User | None, puzzle_id: str
) -> PuzzleAttempt | None:
    """A signed-in player's attempt, to resume; guests have none."""
    if current_user is None:
        return None
    return crud_puzzle_attempt.get_for_puzzle(db, current_user.id, puzzle_id)


def _outcome(attempt: PuzzleAttempt | None) -> tuple[bool, bool]:
    """(won, lost) for a saved attempt."""
    if attempt is None:
        return False, False
    return attempt.won, attempt.completed and not attempt.won


def _open_attempt(
    db: Session, current_user: User | None, puzzle_id: str
) -> PuzzleAttempt | None:
    """A signed-in player's attempt so far, refusing further moves once it has an outcome."""
    attempt = _saved_attempt(db, current_user, puzzle_id)
    if attempt is not None and attempt.completed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You've already finished this puzzle",
        )
    return attempt


def _rows_after_move(
    current_user: User | None,
    attempt: PuzzleAttempt | None,
    sent_count: int | None,
    starter_rows: int,
) -> int:
    """Rows on the board once this move lands, counting any fixed starter row. The server
    counts a signed-in player's own moves; nothing is recorded for guests, so their count stands.
    """
    if current_user is None:
        return sent_count or 0
    return (len(attempt.guesses) if attempt else 0) + starter_rows + 1


# --- stats -------------------------------------------------------------


@router.get("/me/stats", response_model=PuzzleStats)
def my_puzzle_stats(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> PuzzleStats:
    return crud_puzzle_attempt.get_stats(db, current_user.id)


@router.get("/me/history", response_model=PuzzleHistory)
def my_puzzle_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> PuzzleHistory:
    return crud_puzzle_attempt.get_history(db, current_user.id, skip=skip, limit=limit)


# --- word -------------------------------------------------------------


@router.get("/word", response_model=WordPuzzlePublic)
def get_word_puzzle(
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> WordPuzzlePublic:
    # Today's word is reserved for signed-in players; anonymous visitors get yesterday's.
    day = puzzle_logic.today()
    if current_user is None:
        day -= timedelta(days=1)
    answer = _puzzle(db, "word", day).data["answer"]
    puzzle_id = puzzle_logic.puzzle_id("word", day)

    # Signed-in players resume where they left off, finished or not.
    attempt = _saved_attempt(db, current_user, puzzle_id)
    won, lost = _outcome(attempt)

    return WordPuzzlePublic(
        puzzle_id=puzzle_id,
        word_length=puzzle_logic.WORD_LENGTH,
        max_attempts=puzzle_logic.WORD_MAX_ATTEMPTS,
        guesses=[
            WordGuessPublic(guess=guess, grades=puzzle_logic.grade_word(guess, answer))
            for guess in (attempt.guesses if attempt else [])
        ],
        won=won,
        lost=lost,
        answer=answer if lost else None,
    )


@router.post("/word/hint", response_model=WordHint)
def word_hint(
    body: WordHintRequest,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> WordHint:
    row = _move_puzzle(db, "word", body.puzzle_id)
    answer = row.data["answer"]

    if current_user is not None:
        crud_puzzle_attempt.get_or_create(db, current_user.id, "word", body.puzzle_id)

    return WordHint(letter=answer[0])


@router.post("/word/guess", response_model=WordGuessResult)
def word_guess(
    body: WordGuessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> WordGuessResult:
    row = _move_puzzle(db, "word", body.puzzle_id)
    answer = row.data["answer"]
    attempt = _open_attempt(db, current_user, body.puzzle_id)
    if not wordle.is_valid_word(body.guess):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid word"
        )
    grades = puzzle_logic.grade_word(body.guess, answer)
    won = body.guess.casefold() == answer.casefold()
    rows = _rows_after_move(current_user, attempt, body.attempt_count, starter_rows=0)
    lost = not won and rows >= puzzle_logic.WORD_MAX_ATTEMPTS

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db,
            current_user.id,
            "word",
            body.puzzle_id,
            won=won,
            completed=won or lost,
            guess=body.guess,
        )

    return WordGuessResult(
        grades=grades, won=won, lost=lost, answer=answer if lost else None
    )


# --- sudoku -------------------------------------------------------------


@router.get("/sudoku", response_model=SudokuPuzzlePublic)
def get_sudoku_puzzle(
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> SudokuPuzzlePublic:
    day = puzzle_logic.today()
    row = _puzzle(db, "sudoku", day)
    puzzle_id = puzzle_logic.puzzle_id("sudoku", day)

    # Placements aren't stored, so only a won game resumes: its board is the solution.
    won, _ = _outcome(_saved_attempt(db, current_user, puzzle_id))

    return SudokuPuzzlePublic(
        puzzle_id=puzzle_id,
        puzzle=row.data["puzzle"],
        won=won,
        board=row.data["solution"] if won else None,
    )


def _sudoku_turn(
    db: Session,
    current_user: User | None,
    row: Puzzle,
    puzzle_id: str,
    sent_board: list[int],
    filled: int | None,
) -> bool:
    """Record a sudoku move that filled cell `filled` (None for a wrong one) and say
    whether the puzzle is solved. A signed-in player's board is the puzzle plus the
    cells the server saw them fill; a guest's is the one the client sent, as nothing is
    recorded for guests."""
    solution = row.data["solution"]
    if current_user is None:
        board = list(sent_board)
        if filled is not None:
            board[filled] = solution[filled]
        return bool(board == solution)

    attempt = crud_puzzle_attempt.get_for_puzzle(db, current_user.id, puzzle_id)
    cells = {int(cell) for cell in attempt.guesses} if attempt else set()
    # Only a blank cell's first fill is saved; givens are already on the board.
    new_cell = None
    if filled is not None and filled not in cells and row.data["puzzle"][filled] == 0:
        new_cell = str(filled)
        cells.add(filled)
    completed = bool(
        puzzle_logic.sudoku_board(row.data["puzzle"], solution, cells) == solution
    )
    crud_puzzle_attempt.record_attempt(
        db,
        current_user.id,
        "sudoku",
        puzzle_id,
        won=completed,
        completed=completed,
        guess=new_cell,
    )
    return completed


@router.post("/sudoku/move", response_model=SudokuMoveResult)
def sudoku_move(
    body: SudokuMoveCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> SudokuMoveResult:
    row = _move_puzzle(db, "sudoku", body.puzzle_id)
    correct = row.data["solution"][body.index] == body.value
    completed = _sudoku_turn(
        db,
        current_user,
        row,
        body.puzzle_id,
        body.board,
        body.index if correct else None,
    )
    return SudokuMoveResult(correct=correct, completed=completed)


@router.post("/sudoku/hint", response_model=SudokuHintResult)
def sudoku_hint(
    body: SudokuHintCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> SudokuHintResult:
    row = _move_puzzle(db, "sudoku", body.puzzle_id)
    completed = _sudoku_turn(
        db, current_user, row, body.puzzle_id, body.board, body.index
    )
    return SudokuHintResult(value=row.data["solution"][body.index], completed=completed)


# --- memory -------------------------------------------------------------


@router.get("/memory", response_model=MemoryPuzzlePublic)
def get_memory_puzzle(
    db: Session = Depends(deps.get_db),
) -> MemoryPuzzlePublic | HTTPException:
    row = crud_puzzle.get_by_game_variant(db, "memory", 0)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No memory puzzle seeded"
        )
    symbols = row.data["symbols"]
    # The deck is shuffled from the id, so one id per day deals everyone the same deck.
    return MemoryPuzzlePublic(
        puzzle_id=puzzle_logic.puzzle_id("memory", puzzle_logic.today()),
        size=len(symbols) * 2,
    )


@router.post("/memory/reveal", response_model=MemoryRevealResult)
def memory_reveal(
    body: MemoryRevealCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> MemoryRevealResult | HTTPException:
    _move_day("memory", body.puzzle_id)
    row = crud_puzzle.get_by_game_variant(db, "memory", 0)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No memory puzzle seeded"
        )

    deck = puzzle_logic.seeded_memory_deck(body.puzzle_id, row.data["symbols"])
    if body.index >= len(deck):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid memory card index"
        )

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db, current_user.id, "memory", body.puzzle_id
        )

    return MemoryRevealResult(symbol=deck[body.index])


# --- cipher -------------------------------------------------------------


@router.get("/cipher", response_model=CipherPuzzlePublic)
def get_cipher_puzzle(
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> CipherPuzzlePublic:
    day = puzzle_logic.today()
    answer = _puzzle(db, "cipher", day).data["digits"]
    puzzle_id = puzzle_logic.puzzle_id("cipher", day)

    # Signed-in players resume where they left off, finished or not.
    attempt = _saved_attempt(db, current_user, puzzle_id)
    won, lost = _outcome(attempt)
    attempts = []
    for code in attempt.guesses if attempt else []:
        values = puzzle_logic.decode_cipher(code)
        feedback = puzzle_logic.cipher_feedback(values, answer)
        attempts.append(
            CipherAttemptPublic(
                attempt=values, exact=feedback["exact"], close=feedback["close"]
            )
        )

    return CipherPuzzlePublic(
        puzzle_id=puzzle_id,
        slots=len(answer),
        max_attempts=puzzle_logic.CIPHER_MAX_ATTEMPTS,
        initial_attempt=puzzle_logic.CIPHER_STARTER,
        initial_feedback=CipherFeedback(
            **puzzle_logic.cipher_feedback(puzzle_logic.CIPHER_STARTER, answer)
        ),
        attempts=attempts,
        won=won,
        lost=lost,
        answer=answer if lost else None,
    )


@router.post("/cipher/attempt", response_model=CipherAttemptResult)
def cipher_attempt(
    body: CipherAttemptCreate,
    db: Session = Depends(deps.get_db),
    current_user: User | None = Depends(deps.get_optional_current_user),
) -> CipherAttemptResult:
    row = _move_puzzle(db, "cipher", body.puzzle_id)
    answer = row.data["digits"]
    attempt = _open_attempt(db, current_user, body.puzzle_id)

    feedback = puzzle_logic.cipher_feedback(body.attempt, answer)
    won = feedback["won"]
    rows = _rows_after_move(current_user, attempt, body.attempt_count, starter_rows=1)
    lost = not won and rows >= puzzle_logic.CIPHER_MAX_ATTEMPTS

    if current_user is not None:
        crud_puzzle_attempt.record_attempt(
            db,
            current_user.id,
            "cipher",
            body.puzzle_id,
            won=won,
            completed=won or lost,
            guess=puzzle_logic.encode_cipher(body.attempt),
        )

    return CipherAttemptResult(**feedback, lost=lost, answer=answer if lost else None)
