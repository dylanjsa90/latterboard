import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.routing import APIRoute
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis import asyncio as aioredis

from app.api import deps
from app.api.main import api_router
from app.connection_manager import manager
from app.core.config import APP_ENV, settings
from app.database import SessionLocal
from app.init_db import init_db
from app.models import Match, User
from app.SecurityMiddleware import SecurityHeadersMiddleware

from .init_logger import config_logging

logger = logging.getLogger("uvicorn")
config_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    redis = aioredis.from_url(settings.REDIS_URL)
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    logging.info("Instantiating SessionLocal")
    db = SessionLocal()
    try:
        init_db(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.error("Error initializing database. rolling back changes.")
    finally:
        logger.info("Closing DB session")
        db.close()
    await manager.start()
    yield
    await manager.stop()


def custom_generate_unique_id(route: APIRoute) -> str:
    if len(route.tags) > 0:
        return f"{route.tags[0]}-{route.name}"
    else:
        return f"{route.name}"


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url="/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
) 

# Set all CORS enabled origins
if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return "Hello, World"


app.include_router(api_router, prefix=settings.API_V1_STR, tags=[])

async def _is_match_participant(user_id: int, match_id: int) -> bool:
    db = SessionLocal()
    try:
        match = db.get(Match, match_id)
        return match is not None and user_id in (match.inviter_id, match.invitee_id)
    finally:
        db.close()


@app.websocket("/ws/{game_name}")
async def websocket_endpoint(
    websocket: WebSocket,
    game_name: str,
    current_user: User = Depends(deps.get_current_user_ws),
):
    await manager.connect(websocket, game_name, current_user.username)
    await manager.join(websocket, f"user:{current_user.id}", current_user.username)
    try:
        await websocket.send_json(
            {"type": "connected", "topic": game_name, "username": current_user.username}
        )
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except ValueError:
                frame = None
            msg_type = frame.get("type") if isinstance(frame, dict) else None

            if msg_type == "join_match":
                match_id = frame.get("match_id")
                if isinstance(match_id, int) and await _is_match_participant(
                    current_user.id, match_id
                ):
                    await manager.join(
                        websocket, f"match:{match_id}", current_user.username
                    )
                    await websocket.send_json(
                        {"type": "joined_match", "match_id": match_id}
                    )
            elif msg_type == "leave_match":
                match_id = frame.get("match_id")
                if isinstance(match_id, int):
                    await manager.disconnect(websocket, f"match:{match_id}")
            else:
                await manager.broadcast(
                    {
                        "type": "message",
                        "topic": game_name,
                        "username": current_user.username,
                        "data": raw,
                    },
                    topic=game_name,
                )
    except WebSocketDisconnect:
        logger.info("Websocket left topic %s (%s)", game_name, current_user.username)
    finally:
        await manager.disconnect_all(websocket)