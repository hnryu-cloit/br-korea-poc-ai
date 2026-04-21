from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.dependencies import get_market_insight_service, verify_token
from api.error_contract import build_error_detail
from schemas.contracts import MarketInsightsRequest, MarketInsightsResponse
from services.market_insight_service import MarketInsightService

router = APIRouter(prefix="/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


@router.post(
    "/market/insights",
    response_model=MarketInsightsResponse,
    dependencies=[Depends(verify_token)],
)
async def generate_market_insights(
    payload: MarketInsightsRequest,
    request: Request,
    service: MarketInsightService = Depends(get_market_insight_service),
) -> MarketInsightsResponse:
    """상권 집계 데이터 기반으로 실행 인사이트를 생성합니다."""
    try:
        result = await asyncio.to_thread(
            service.generate,
            audience=payload.audience,
            scope=payload.scope,
            market_data=payload.market_data,
            branch_snapshots=payload.branch_snapshots,
            store_name=payload.store_name,
        )
        result["trace_id"] = getattr(request.state, "request_id", None)
        return MarketInsightsResponse(**result)
    except (ValueError, TypeError, RuntimeError) as exc:
        logger.exception("상권 인사이트 생성 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="MARKET_INSIGHTS_FAILED",
                message="상권 인사이트 생성에 실패했습니다.",
                retryable=True,
            ),
        ) from exc
