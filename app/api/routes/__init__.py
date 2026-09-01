from app.api.routes.login import router as login_router
from app.api.routes.scores import router as scores_router
from app.api.routes.users import router as users_router

from app.api.routes.users import router as users_router
from app.api.routes.matches import router as matches_router
__all__ = ["users_router", "login_router", "scores_router", "matches_router"]
