import logging
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import FastAPI

from api.config import get_settings
from api.routers import management

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    if settings.APP_ENV != "local" and not settings.AI_SERVICE_TOKEN:
        raise RuntimeError("AI_SERVICE_TOKEN is required when APP_ENV is not local.")
    if settings.APP_ENV == "local" and not settings.AI_SERVICE_TOKEN:
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

app.include_router(management.router)

for router_module in ("api.routers.sales", "api.routers.home", "api.routers.generation"):
    try:
        module = import_module(router_module)
    except ModuleNotFoundError as exc:
        logger.warning("%s 라우터를 불러오지 못해 제외합니다: %s", router_module, exc)
        continue
    app.include_router(module.router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
