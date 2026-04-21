import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.dependencies import (
    get_ordering_service,
    get_production_service,
    get_sales_service,
)
from schemas.dashboard import HomeDashboardResponse
from services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/home", tags=["home"])
logger = logging.getLogger(__name__)


class HomeOverviewRequest(BaseModel):
    store_id: str
    target_date: str
    inventory_data: list[dict[str, Any]] = []
    production_data: list[dict[str, Any]] = []
    sales_data: list[dict[str, Any]] = []
    store_production_data: list[dict[str, Any]] = []


@router.post("/overview", response_model=HomeDashboardResponse)
async def get_home_overview(
    body: HomeOverviewRequest,
    prod_service=Depends(get_production_service),
    order_service=Depends(get_ordering_service),
    sales_service=Depends(get_sales_service),
) -> HomeDashboardResponse:
    """매장 홈 대시보드 통합 정보를 반환합니다."""
    try:
        payload = {
            "store_id": body.store_id,
            "target_date": body.target_date,
            "current_time": datetime.now(),
        }

        logger.info(f"홈 대시보드 요청 - 매장: {body.store_id}, 날짜: {body.target_date}")

        dash_service = DashboardService(prod_service, order_service, sales_service)

        raw_data = {
            "inventory_data": body.inventory_data,
            "production_data": body.production_data,
            "sales_data": body.sales_data,
            "store_production_data": body.store_production_data,
        }

        result = await asyncio.to_thread(dash_service.get_home_overview, payload, raw_data)
        return result

    except (ValueError, TypeError, RuntimeError) as exc:
        logger.exception("홈 대시보드 생성 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="홈 화면 데이터를 불러오지 못했습니다.",
        ) from exc
