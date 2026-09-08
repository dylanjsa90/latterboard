from fastapi import APIRouter

from app.api.routes import login, matches, scores, users, ws
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(scores.router)
api_router.include_router(matches.router)
api_router.include_router(ws.router)
# api_router.include_router(utils.router)
# api_router.include_router(items.router)


if settings.ENVIRONMENT == "local":
    pass
