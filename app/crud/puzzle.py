from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.game.puzzle_seed_data import (
    CIPHERS,
    MEMORY_SYMBOLS,
    SUDOKU_VARIANTS,
    choose_word,
    generate_cipher_digits,
    shuffled_words,
    sudoku_variant,
)
from app.game.puzzles import today as utc_today
from app.models.puzzle import Puzzle
from app.schemas.puzzle import PuzzleCreate, PuzzleUpdate


class CRUDPuzzle(CRUDBase[Puzzle, PuzzleCreate, PuzzleUpdate]):
    def get_by_game_variant(self, db: Session, game: str, variant_index: int) -> Puzzle | None:
        return (
            db.query(Puzzle)
            .filter(Puzzle.game == game, Puzzle.variant_index == variant_index)
            .first()
        )

    def get_by_game_date(self, db: Session, game: str, puzzle_date: date) -> Puzzle | None:
        return (
            db.query(Puzzle).filter(Puzzle.game == game, Puzzle.date == puzzle_date).first()
        )

    def count_by_game(self, db: Session, game: str) -> int:
        return db.query(Puzzle).filter(Puzzle.game == game).count()

    def _next_variant_index(self, db: Session, game: str) -> int:
        max_index = db.query(func.max(Puzzle.variant_index)).filter(Puzzle.game == game).scalar()
        return 0 if max_index is None else max_index + 1

    def _generate_data(self, game: str, variant_index: int) -> dict | None:
        if game == "word":
            return {"answer": choose_word()}
        if game == "cipher":
            return {"digits": generate_cipher_digits()}
        if game == "sudoku":
            return sudoku_variant(variant_index)
        return None

    def get_or_generate(self, db: Session, game: str, puzzle_date: date) -> Puzzle | None:
        """Return the puzzle for this game/date, generating and persisting a
        fresh variant on the fly when the seeded bank doesn't cover it yet
        (e.g. the curated bank has been exhausted). Returns None only for
        games with no known generator (there aren't any today).
        """
        row = self.get_by_game_date(db, game, puzzle_date)
        if row is not None:
            return row

        variant_index = self._next_variant_index(db, game)
        data = self._generate_data(game, variant_index)
        if data is None:
            return None

        row = Puzzle(game=game, variant_index=variant_index, date=puzzle_date, data=data)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # Another request generated this date's puzzle concurrently.
            db.rollback()
            return self.get_by_game_date(db, game, puzzle_date)
        db.refresh(row)
        return row

    def seed_defaults(self, db: Session) -> None:
        """Idempotently populate the puzzle bank from the ported puzzle-box
        content. A no-op per game once rows already exist, so it's safe to
        call on every startup/init. Each variant is assigned its own
        calendar date, starting yesterday (anonymous players are served the
        prior day's word), so a puzzle is served on exactly one day instead of
        cycling/repeating once the bank has been through once.
        """
        start = utc_today() - timedelta(days=1)
        if self.count_by_game(db, "word") == 0:
            for index, word in enumerate(shuffled_words()):
                db.add(
                    Puzzle(
                        game="word",
                        variant_index=index,
                        date=start + timedelta(days=index),
                        data={"answer": word},
                    )
                )
        if self.count_by_game(db, "sudoku") == 0:
            for index, variant in enumerate(SUDOKU_VARIANTS):
                db.add(
                    Puzzle(
                        game="sudoku",
                        variant_index=index,
                        date=start + timedelta(days=index),
                        data=variant,
                    )
                )
        if self.count_by_game(db, "cipher") == 0:
            for index, digits in enumerate(CIPHERS):
                db.add(
                    Puzzle(
                        game="cipher",
                        variant_index=index,
                        date=start + timedelta(days=index),
                        data={"digits": digits},
                    )
                )
        if self.count_by_game(db, "memory") == 0:
            db.add(Puzzle(game="memory", variant_index=0, date=start, data={"symbols": MEMORY_SYMBOLS}))
        db.commit()


puzzle = CRUDPuzzle(Puzzle)
