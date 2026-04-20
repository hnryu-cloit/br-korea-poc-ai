import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_channel_payment_analyzer, get_sales_analyzer, verify_token
from schemas.contracts import (
    ProfitabilitySimulationRequest,
    ProfitabilitySimulationResponse,
    SalesPromptSuggestRequest,
    SalesPromptSuggestResponse,
    SalesQueryRequest,
    SalesQueryResponse,
)
from services.channel_payment_analyzer import ChannelPaymentAnalyzer
from services.sales_analyzer import SalesAnalyzer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("/query", response_model=SalesQueryResponse, dependencies=[Depends(verify_token)])
async def query_sales(
    payload: SalesQueryRequest, analyzer: SalesAnalyzer = Depends(get_sales_analyzer)
) -> SalesQueryResponse:
    """자연어 매출 질의를 SalesAnalyzer에 위임해 분석 응답을 반환합니다."""
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


@router.post(
    "/prompts/suggest",
    response_model=SalesPromptSuggestResponse,
    dependencies=[Depends(verify_token)],
)
async def suggest_sales_prompts(
    payload: SalesPromptSuggestRequest,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> SalesPromptSuggestResponse:
    try:
        logger.info("추천 질문 생성 요청: store=%s domain=%s", payload.store_id, payload.domain)
        return await asyncio.to_thread(analyzer.suggest_prompts, payload)
    except Exception as exc:
        logger.exception("추천 질문 생성 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="추천 질문 생성에 실패했습니다.",
        ) from exc


@router.post(
    "/profitability",
    response_model=ProfitabilitySimulationResponse,
    dependencies=[Depends(verify_token)],
)
async def get_profitability_simulation(
    payload: ProfitabilitySimulationRequest,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> ProfitabilitySimulationResponse:
    """수익성 시뮬레이션 요청을 SalesAnalyzer에 위임해 결과를 반환합니다."""
    try:
        logger.info(
            "수익성 시뮬레이션 요청: 매장 %s (%s ~ %s)",
            payload.store_id,
            payload.date_from,
            payload.date_to,
        )
        return await asyncio.to_thread(
            analyzer.simulate_profitability,
            payload.store_id,
            payload.date_from,
            payload.date_to,
        )
    except Exception as exc:
        logger.exception("수익성 시뮬레이션 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="수익성 시뮬레이션에 실패했습니다.",
        ) from exc


@router.post(
    "/query/channel-payment",
    response_model=SalesQueryResponse,
    dependencies=[Depends(verify_token)],
)
async def query_channel_payment(
    payload: SalesQueryRequest,
    analyzer: ChannelPaymentAnalyzer = Depends(get_channel_payment_analyzer),
) -> SalesQueryResponse:
    """채널·결제수단 특화 분석 질의를 ChannelPaymentAnalyzer에 위임해 응답을 반환합니다."""
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
