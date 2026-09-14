from typing import Literal

from pydantic import BaseModel

from app.schemas.match import MatchPublic


class MatchmakingStatus(BaseModel):
    # "queued": still waiting for an opponent. "matched": paired, and `match` has started.
    status: Literal["queued", "matched"]
    match: MatchPublic | None = None
    # A queued player who doesn't re-join within this many seconds is dropped from the
    # queue, so clients re-POST well inside it to stay queued.
    queue_ttl_seconds: int
