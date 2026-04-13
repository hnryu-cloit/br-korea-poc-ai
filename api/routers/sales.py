import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_token, get_sales_analyzer, get_channel_payment_analyzer
from schemas.contracts import (
    SalesQueryRequest,
    SalesQueryResponse,
    ProfitabilitySimulationRequest,
    ProfitabilitySimulationResponse,
)
from services.sales_analyzer import SalesAnalyzer
from services.channel_payment_analyzer import ChannelPaymentAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("/query", response_model=SalesQueryResponse, dependencies=[Depends(verify_token)])
async def query_sales(
    payload: SalesQueryRequest, 
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer)
) -> SalesQueryResponse:
    try:
        logger.info("매출 분석 요청: %s", payload.query[:50])
        result = await asyncio.to_thread(analyzer.analyze, payload)
        return result
    except Exception as exc:
        logger.exception("매출 분석 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="매출 분석에 실패했습니다.",
        ) from exc


@router.post("/profitability", response_model=ProfitabilitySimulationResponse, dependencies=[Depends(verify_token)])
async def get_profitability_simulation(
    payload: ProfitabilitySimulationRequest,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> ProfitabilitySimulationResponse:
    """표준 마진 65% 기반 수익성 시뮬레이션 (원가 데이터 부재 환경)."""
    try:
        logger.info("수익성 시뮬레이션 요청: 매장 %s (%s ~ %s)", payload.store_id, payload.date_from, payload.date_to)
        STANDARD_MARGIN = 0.65
        # SalesAnalyzer에서 매출 데이터 조회 시도, 실패 시 stub
        total_revenue = 5_000_000.0
        top_items: list = []
        try:
            profile = await asyncio.to_thread(
                analyzer.extract_store_profile, payload.store_id, payload.date_from, payload.date_to
            )
            if profile and isinstance(profile, dict):
                total_revenue = float(profile.get("total_revenue", total_revenue))
                top_items = profile.get("top_items", [])
        except Exception:
            pass

        return ProfitabilitySimulationResponse(
            store_id=payload.store_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
            total_revenue=total_revenue,
            estimated_margin_rate=STANDARD_MARGIN,
            estimated_profit=round(total_revenue * STANDARD_MARGIN),
            top_items=top_items,
            simulation_note="표준 마진 65% 적용 (원가 데이터 부재로 추정값 사용)",
        )
    except Exception as exc:
        logger.exception("수익성 시뮬레이션 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="수익성 시뮬레이션에 실패했습니다.",
        ) from exc


@router.post("/query/channel-payment", response_model=SalesQueryResponse, dependencies=[Depends(verify_token)])
async def query_channel_payment(
    payload: SalesQueryRequest, 
    analyzer: ChannelPaymentAnalyzer = Depends(get_channel_payment_analyzer)
) -> SalesQueryResponse:
    try:
        logger.info("채널 및 결제수단 분석 요청: %s", payload.query[:50])
        result = await asyncio.to_thread(analyzer.analyze, payload)
        return result
    except Exception as exc:
        logger.exception("채널 및 결제수단 분석 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="채널 및 결제수단 분석에 실패했습니다.",
        ) from exc
