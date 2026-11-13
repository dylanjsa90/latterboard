import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.routing import APIRoute
from loguru import logger

from app.api.main import api_router
from app.api.routes import ws
from app.connection_manager import manager
from app.core.config import settings
from app.core.loop_lag import watch_loop_lag
from app.core.middleware import SecurityHeadersMiddleware
from app.database import SessionLocal
from app.init_db import init_db
from app.init_logger import setup_loguru


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    setup_loguru()
    logger.info("Instantiating SessionLocal")
    db = SessionLocal()
    try:
        init_db(db)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error initializing database. rolling back changes.")
    finally:
        logger.info("Closing DB session")
        db.close()
    logger.info("Starting connnection manager")
    await manager.start()
    lag_watch = asyncio.create_task(watch_loop_lag())
    yield
    lag_watch.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await lag_watch
    logger.info("Stopping connnection manager")
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


app.add_middleware(SecurityHeadersMiddleware)  # type: ignore[arg-type]
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router, prefix=settings.API_V1_STR, tags=[])
# The socket itself lives at /ws/{game_name}, outside /api/v1.
app.include_router(ws.socket_router)
