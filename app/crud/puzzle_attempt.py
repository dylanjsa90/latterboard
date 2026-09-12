from collections import Counter
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.puzzle import PuzzleAttempt
from app.schemas.puzzle import (
    GamePuzzleStats,
    PuzzleAttemptCreate,
    PuzzleAttemptUpdate,
    PuzzleHistory,
    PuzzleHistoryItem,
    PuzzleStats,
    RecentPuzzleAttempt,
)
from app.utils import utcnow

RECENT_ATTEMPTS_LIMIT = 20


def _longest_run(days: set[date]) -> int:
    best = 0
    for day in days:
        # Walk each run once, starting from its first day.
        if day - timedelta(days=1) in days:
            continue
        length = 1
        while day + timedelta(days=length) in days:
            length += 1
        best = max(best, length)
    return best


class CRUDPuzzleAttempt(CRUDBase[PuzzleAttempt, PuzzleAttemptCreate, PuzzleAttemptUpdate]):
    def get_or_create(
        self, db: Session, user_id: int, game: str, puzzle_id: str
    ) -> PuzzleAttempt:
        obj = (
            db.query(PuzzleAttempt)
            .filter(PuzzleAttempt.user_id == user_id, PuzzleAttempt.puzzle_id == puzzle_id)
            .first()
        )
        if obj is None:
            obj = PuzzleAttempt(user_id=user_id, game=game, puzzle_id=puzzle_id)
            db.add(obj)
            db.commit()
            db.refresh(obj)
        return obj

    def record_attempt(
        self,
        db: Session,
        user_id: int,
        game: str,
        puzzle_id: str,
        *,
        won: bool = False,
        completed: bool = False,
    ) -> PuzzleAttempt:
        obj = self.get_or_create(db, user_id, game, puzzle_id)
        obj.attempt_count += 1
        obj.won = obj.won or won
        if completed and not obj.completed:
            obj.completed = True
            obj.completed_at = utcnow()
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def get_completed(self, db: Session, user_id: int) -> list[PuzzleAttempt]:
        return (
            db.query(PuzzleAttempt)
            .filter(PuzzleAttempt.user_id == user_id, PuzzleAttempt.completed.is_(True))
            .order_by(PuzzleAttempt.completed_at.desc())
            .all()
        )

    def get_stats(self, db: Session, user_id: int) -> PuzzleStats:
        # get_completed only returns rows with completed=True, which record_attempt
        # always pairs with a completed_at timestamp, so this drops nothing in
        # practice — pairing it up front just satisfies the column's nullable type.
        completed = [
            (a, a.completed_at)
            for a in self.get_completed(db, user_id)
            if a.completed_at is not None
        ]
        today = utcnow().date()

        solved_dates = {completed_at.date() for _, completed_at in completed}
        streak = 0
        cursor = today
        while cursor in solved_dates:
            streak += 1
            cursor -= timedelta(days=1)

        played = Counter(a.game for a, _ in completed)
        won = Counter(a.game for a, _ in completed if a.won)

        return PuzzleStats(
            total_solved=len(completed),
            today_solved=sum(
                1 for _, completed_at in completed if completed_at.date() == today
            ),
            streak=streak,
            best_streak=_longest_run(solved_dates),
            games=[
                GamePuzzleStats(game=game, played=count, won=won[game])
                for game, count in sorted(played.items())
            ],
            recent=[
                RecentPuzzleAttempt(
                    game=a.game,
                    solved_on=completed_at.date(),
                    solved_at=completed_at,
                    result=a.attempt_count,
                )
                for a, completed_at in completed[:RECENT_ATTEMPTS_LIMIT]
            ],
        )

    def get_history(
        self, db: Session, user_id: int, *, skip: int, limit: int
    ) -> PuzzleHistory:
        query = db.query(PuzzleAttempt).filter(
            PuzzleAttempt.user_id == user_id, PuzzleAttempt.completed.is_(True)
        )
        rows = (
            # id breaks ties so pages stay stable when two rows share a timestamp.
            query.order_by(PuzzleAttempt.completed_at.desc(), PuzzleAttempt.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return PuzzleHistory(
            total=query.count(),
            # Same pairing as get_stats: completed rows always carry completed_at.
            items=[
                PuzzleHistoryItem(
                    game=a.game,
                    puzzle_id=a.puzzle_id,
                    won=a.won,
                    attempt_count=a.attempt_count,
                    completed_at=a.completed_at,
                )
                for a in rows
                if a.completed_at is not None
            ],
        )


puzzle_attempt = CRUDPuzzleAttempt(PuzzleAttempt)
