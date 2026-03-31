import logging
import warnings
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from api.config import get_settings
from api.routers import generation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    if not settings.AI_SERVICE_TOKEN:
        warnings.warn("AI_SERVICE_TOKEN 미설정 — 로컬 개발 전용으로만 사용하세요.", stacklevel=1)
    logger.info("br-korea-poc AI Service 시작")
    yield
    logger.info("br-korea-poc AI Service 종료")


app = FastAPI(
    title="br-korea-poc AI Service",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.include_router(generation.router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}