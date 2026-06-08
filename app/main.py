import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from fastapi_cache.decorator import cache
from redis import asyncio as aioredis

import app.api.deps
import app.crud as crud
import app.models as models
import app.schemas as schemas
from app.api.main import api_router
from app.core.config import APP_ENV, settings
from app.database import SessionLocal
from app.init_db import init_db

from .init_logger import config_logging

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, filename="./logs_backend.log", filemode="a", format="%(asctime)-15s %(levelname)-8s %(message)s")
    config_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = aioredis.from_url(settings.REDIS_URL)
    FastAPICache.init(RedisBackend(redis), prefix="fastapi-cache")
    yield

_local = APP_ENV == "local"

def custom_generate_unique_id(route: APIRoute) -> str:
    if len(route.tags) > 0:
        return f"{route.tags[0]}-{route.name}"
    else:
        return f"{route.name}"

@cache()
async def get_cache():
    return 1

app = FastAPI(
    title="Games API",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=custom_generate_unique_id,
)

@app.on_event("startup")
async def startup():
    logging.info("Instantiating SessionLocal")
    db = SessionLocal()
    try:
        init_db(db)
        db.commit()
    finally:
        db.close()

# Set all CORS enabled origins
if settings.all_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.all_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/")
async def root():
  return "Hello, World"

app.include_router(api_router, prefix=settings.API_V1_STR, tags=["domain"])


