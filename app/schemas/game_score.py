from datetime import datetime

from pydantic import BaseModel, Field, model_validator

# (step, highest) for the games whose scoring the server knows. Snake scores 10 a food,
# and its 20x20 board fits 399 foods beside the starting head; webcade's snake
# constants must agree. Other games are only held to non-negative scores.
SCORE_RULES: dict[str, tuple[int, int]] = {"snake": (0, 3990)}


class GameScoreCreate(BaseModel):
    game: str
    score: int = Field(ge=0)

    @model_validator(mode="after")
    def check_reachable(self) -> "GameScoreCreate":
        rule = SCORE_RULES.get(self.game)
        if rule is not None:
            step, highest = rule
            if self.score > highest or self.score % step:
                raise ValueError(f"{self.score} is not a reachable {self.game} score.")
        return self


class GameScorePublic(BaseModel):
    id: int
    game: str
    score: int
    created_at: datetime
    model_config = {"from_attributes": True}


class GameScoreInDB(GameScoreCreate):
    user_id: int


class GameScoreUpdate(BaseModel):
    pass


class PlaysToday(BaseModel):
    used: int
    limit: int


class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    score: int
    achieved_at: datetime
