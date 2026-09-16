from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.crud.game_score import game_score as crud_score
from app.game.puzzles import today
from app.models import User
from app.models.game_score import GameScore
from app.schemas.game_score import (
    GameScoreCreate,
    GameScorePublic,
    LeaderboardEntry,
    PlaysToday,
)

router = APIRouter(prefix="/scores", tags=["scores"])


@router.post("/", response_model=GameScorePublic, status_code=status.HTTP_201_CREATED)
def submit_score(
    score_in: GameScoreCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> GameScore:
    pacific_today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    count = crud_score.get_daily_play_count(
        db, current_user.id, score_in.game, pacific_today
    )
    if count >= settings.MAX_DAILY_PLAYS_PER_GAME:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily play limit reached.",
        )
    return crud_score.create_score(db, current_user.id, score_in)


@router.get("/leaderboard/{game}/all-time", response_model=list[LeaderboardEntry])
def leaderboard_alltime(
    game: str,
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(deps.get_db),
) -> list[LeaderboardEntry]:
    rows = crud_score.get_leaderboard_alltime(db, game, limit)
    return [
        LeaderboardEntry(rank=i, username=u, score=s, achieved_at=a)
        for i, (u, s, a) in enumerate(rows, 1)
    ]


@router.get("/leaderboard/{game}/daily", response_model=list[LeaderboardEntry])
def leaderboard_daily(
    game: str,
    day: Optional[date] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(deps.get_db),
) -> list[LeaderboardEntry]:
    rows = crud_score.get_leaderboard_daily(
        db, game, day or datetime.now(timezone.utc).date(), limit
    )
    return [
        LeaderboardEntry(rank=i, username=u, score=s, achieved_at=a)
        for i, (u, s, a) in enumerate(rows, 1)
    ]


@router.get("/leaderboard/{game}/monthly", response_model=list[LeaderboardEntry])
def leaderboard_monthly(
    game: str,
    year: Optional[int] = Query(default=None),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(deps.get_db),
) -> list[LeaderboardEntry]:
    today_utc = datetime.now(timezone.utc).date()
    rows = crud_score.get_leaderboard_monthly(
        db, game, year or today_utc.year, month or today_utc.month, limit
    )
    return [
        LeaderboardEntry(rank=i, username=u, score=s, achieved_at=a)
        for i, (u, s, a) in enumerate(rows, 1)
    ]


@router.get("/me/{game}/daily-count")
def my_daily_count(
    game: str,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
) -> dict[str, int]:
    pacific_today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    count = crud_score.get_daily_play_count(db, current_user.id, game, pacific_today)
    return {"count": count}


@router.get("/me/{game}", response_model=list[GameScorePublic])
def my_scores(
    game: str,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
) -> list[GameScore]:
    return crud_score.get_user_scores(db, current_user.id, game)


@router.get("/me/{game}/plays-today", response_model=PlaysToday)
def my_plays_today(
    game: str,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(deps.get_db),
) -> PlaysToday:
    """Scores saved today (UTC) are plays; POST / answers 429 once `used` hits `limit`."""
    used = crud_score.get_daily_play_count(db, current_user.id, game, today())
    return PlaysToday(used=used, limit=settings.MAX_DAILY_PLAYS_PER_GAME)
