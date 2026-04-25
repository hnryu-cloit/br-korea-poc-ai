import asyncio
import logging

from fastapi import APIRouter, Depends, Request

from api.dependencies import (
    get_channel_payment_analyzer,
    get_insight_summarize_service,
    get_sales_analyzer,
    verify_token,
)
from api.error_contract import router_error_handler
from schemas.contracts import (
    CampaignNarrativeRequest,
    CampaignNarrativeResponse,
    InsightSummarizeRequest,
    InsightSummarizeResponse,
    MenuInsightsRequest,
    MenuInsightsResponse,
    ProfitabilitySimulationRequest,
    ProfitabilitySimulationResponse,
    SalesPromptSuggestRequest,
    SalesPromptSuggestResponse,
    SalesQueryRequest,
    SalesQueryResponse,
)
from services.channel_payment_analyzer import ChannelPaymentAnalyzer
from services.insight_summarize_service import InsightSummarizeService
from services.sales_analyzer import SalesAnalyzer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("/query", dependencies=[Depends(verify_token)])
async def query_sales(
    payload: SalesQueryRequest,
    request: Request,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> SalesQueryResponse:
    """자연어 매출 질의를 SalesAnalyzer에 위임해 분석 응답을 반환합니다."""
    async with router_error_handler(
        request,
        error_code="SALES_QUERY_FAILED",
        message="매출 분석에 실패했습니다.",
        log_message="매출 분석 중 오류 발생",
    ):
        logger.info("매출 분석 요청: %s", payload.query[:50])
        return await asyncio.to_thread(analyzer.analyze, payload)


@router.post(
    "/prompts/suggest",
    response_model=SalesPromptSuggestResponse,
    dependencies=[Depends(verify_token)],
)
async def suggest_sales_prompts(
    payload: SalesPromptSuggestRequest,
    request: Request,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> SalesPromptSuggestResponse:
    """추천 질문 생성 요청을 SalesAnalyzer에 위임해 결과를 반환합니다."""
    async with router_error_handler(
        request,
        error_code="SALES_PROMPTS_FAILED",
        message="추천 질문 생성에 실패했습니다.",
        log_message="추천 질문 생성 오류",
    ):
        logger.info("추천 질문 생성 요청: store=%s domain=%s", payload.store_id, payload.domain)
        return await asyncio.to_thread(analyzer.suggest_prompts, payload)


@router.post(
    "/profitability",
    response_model=ProfitabilitySimulationResponse,
    dependencies=[Depends(verify_token)],
)
async def get_profitability_simulation(
    payload: ProfitabilitySimulationRequest,
    request: Request,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> ProfitabilitySimulationResponse:
    """수익성 시뮬레이션 요청을 SalesAnalyzer에 위임해 결과를 반환합니다."""
    async with router_error_handler(
        request,
        error_code="SALES_PROFITABILITY_FAILED",
        message="수익성 시뮬레이션에 실패했습니다.",
        log_message="수익성 시뮬레이션 오류",
    ):
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


@router.post(
    "/summarize/insights",
    response_model=InsightSummarizeResponse,
    dependencies=[Depends(verify_token)],
)
async def summarize_insights(
    payload: InsightSummarizeRequest,
    request: Request,
    service: InsightSummarizeService = Depends(get_insight_summarize_service),
) -> InsightSummarizeResponse:
    """매출 인사이트 섹션 요약을 구조화 JSON으로 반환합니다."""
    async with router_error_handler(
        request,
        error_code="INSIGHT_SUMMARIZE_FAILED",
        message="인사이트 섹션 요약 생성에 실패했습니다.",
        log_message="인사이트 섹션 요약 오류",
    ):
        logger.info("인사이트 섹션 요약 요청: store=%s", payload.store_id)
        return await asyncio.to_thread(service.summarize_insights, payload)


@router.post(
    "/summarize/campaign",
    response_model=CampaignNarrativeResponse,
    dependencies=[Depends(verify_token)],
)
async def summarize_campaign(
    payload: CampaignNarrativeRequest,
    request: Request,
    service: InsightSummarizeService = Depends(get_insight_summarize_service),
) -> CampaignNarrativeResponse:
    """캠페인 효과 서술을 구조화 JSON으로 반환합니다."""
    async with router_error_handler(
        request,
        error_code="CAMPAIGN_NARRATIVE_FAILED",
        message="캠페인 서술 생성에 실패했습니다.",
        log_message="캠페인 서술 생성 오류",
    ):
        logger.info("캠페인 서술 생성 요청: store=%s", payload.store_id)
        return await asyncio.to_thread(service.generate_campaign_narrative, payload)


@router.post(
    "/summarize/menu-insights",
    response_model=MenuInsightsResponse,
    dependencies=[Depends(verify_token)],
)
async def generate_menu_insights(
    payload: MenuInsightsRequest,
    request: Request,
    service: InsightSummarizeService = Depends(get_insight_summarize_service),
) -> MenuInsightsResponse:
    """페이지 데이터 기반 메뉴 인사이트 카드 3개를 Gemini로 생성합니다."""
    async with router_error_handler(
        request,
        error_code="MENU_INSIGHTS_FAILED",
        message="메뉴 인사이트 생성에 실패했습니다.",
        log_message="메뉴 인사이트 생성 오류",
    ):
        logger.info("메뉴 인사이트 생성 요청: store=%s", payload.store_id)
        return await asyncio.to_thread(service.generate_menu_insights, payload)


@router.post(
    "/query/channel-payment",
    dependencies=[Depends(verify_token)],
)
async def query_channel_payment(
    payload: SalesQueryRequest,
    request: Request,
    analyzer: ChannelPaymentAnalyzer = Depends(get_channel_payment_analyzer),
) -> SalesQueryResponse:
    """채널·결제수단 특화 분석 질의를 ChannelPaymentAnalyzer에 위임해 응답을 반환합니다."""
    async with router_error_handler(
        request,
        error_code="CHANNEL_PAYMENT_QUERY_FAILED",
        message="채널 및 결제수단 분석에 실패했습니다.",
        log_message="채널 및 결제수단 분석 중 오류 발생",
    ):
        logger.info("채널 및 결제수단 분석 요청: %s", payload.query[:50])
        return await asyncio.to_thread(analyzer.analyze, payload)