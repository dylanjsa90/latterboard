from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import Row, extract, func
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.game_score import GameScore
from app.models.user import User
from app.schemas.game_score import GameScoreCreate, GameScoreUpdate


class CRUDGameScore(CRUDBase[GameScore, GameScoreCreate, GameScoreUpdate]):
    def get_daily_play_count(
        self, db: Session, user_id: int, game: str, today: date
    ) -> int:
        return (
            db.query(GameScore)
            .filter(
                GameScore.user_id == user_id,
                GameScore.game == game,
                func.date(GameScore.created_at) == today.isoformat(),
            )
            .count()
        )

    def create_score(
        self, db: Session, user_id: int, score_in: GameScoreCreate
    ) -> GameScore:
        entry = GameScore(user_id=user_id, game=score_in.game, score=score_in.score)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    def get_user_scores(self, db: Session, user_id: int, game: str) -> list[GameScore]:
        return (
            db.query(GameScore)
            .filter(GameScore.user_id == user_id, GameScore.game == game)
            .order_by(GameScore.created_at.desc())
            .all()
        )

    def _leaderboard_query(
        self, db: Session, game: str, limit: int, time_filter: Any = None
    ) -> Sequence[Row[tuple[str, int, datetime]]]:
        q = db.query(
            GameScore.user_id,
            func.max(GameScore.score).label("best_score"),
            func.min(GameScore.created_at).label("first_achieved"),
        )
        q = q.filter(GameScore.game == game)
        if time_filter is not None:
            q = q.filter(time_filter)
        sq = q.group_by(GameScore.user_id).subquery()
        return cast(
            Sequence[Row[tuple[str, int, datetime]]],
            db.query(User.username, sq.c.best_score, sq.c.first_achieved)
            .join(User, User.id == sq.c.user_id)
            .order_by(sq.c.best_score.desc())
            .limit(limit)
            .all(),
        )

    def get_leaderboard_alltime(
        self, db: Session, game: str, limit: int = 10
    ) -> Sequence[Row[tuple[str, int, datetime]]]:
        return self._leaderboard_query(db, game, limit)

    def get_leaderboard_daily(
        self, db: Session, game: str, day: date, limit: int = 10
    ) -> Sequence[Row[tuple[str, int, datetime]]]:
        return self._leaderboard_query(
            db,
            game,
            limit,
            time_filter=func.date(GameScore.created_at) == day.isoformat(),
        )

    def get_leaderboard_monthly(
        self, db: Session, game: str, year: int, month: int, limit: int = 10
    ) -> Sequence[Row[tuple[str, int, datetime]]]:
        return self._leaderboard_query(
            db,
            game,
            limit,
            time_filter=(
                (extract("year", GameScore.created_at) == year)
                & (extract("month", GameScore.created_at) == month)
            ),
        )


game_score = CRUDGameScore(GameScore)
