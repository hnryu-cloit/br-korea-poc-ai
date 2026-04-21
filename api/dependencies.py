from __future__ import annotations

import logging
import warnings

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings, get_settings

logger = logging.getLogger(__name__)

from common.gemini import Gemini
from services.chance_loss_service import ChanceLossService
from services.channel_payment_analyzer import ChannelPaymentAnalyzer
from services.market_insight_service import MarketInsightService
from services.ordering_history_insight_service import OrderingHistoryInsightService
from services.orchestrator import AgentOrchestrator
from services.ordering_service import OrderingService
from services.production_service import ProductionService
from services.rag_service import RAGService
from services.sales_analyzer import SalesAnalyzer

bearer_scheme = HTTPBearer(auto_error=False)

# Singleton-like Gemini client
_gemini_client = Gemini()


def get_gemini_client() -> Gemini:
    return _gemini_client


def get_rag_service(gemini: Gemini = Depends(get_gemini_client)) -> RAGService:
    return RAGService(gemini_client=gemini)


def get_orchestrator(gemini: Gemini = Depends(get_gemini_client)) -> AgentOrchestrator:
    return AgentOrchestrator(gemini_client=gemini)


def get_sales_analyzer(gemini: Gemini = Depends(get_gemini_client)) -> SalesAnalyzer:
    return SalesAnalyzer(gemini_client=gemini)


def get_channel_payment_analyzer(
    gemini: Gemini = Depends(get_gemini_client),
) -> ChannelPaymentAnalyzer:
    return ChannelPaymentAnalyzer(gemini_client=gemini)


def get_market_insight_service(gemini: Gemini = Depends(get_gemini_client)) -> MarketInsightService:
    return MarketInsightService(gemini_client=gemini)


def get_ordering_history_insight_service(
    gemini: Gemini = Depends(get_gemini_client),
    rag_service: RAGService = Depends(get_rag_service),
) -> OrderingHistoryInsightService:
    return OrderingHistoryInsightService(gemini_client=gemini, rag_service=rag_service)


def get_sales_service(gemini: Gemini = Depends(get_gemini_client)) -> SalesAnalyzer:
    return SalesAnalyzer(gemini_client=gemini)


def get_production_service(gemini: Gemini = Depends(get_gemini_client)) -> ProductionService:
    return ProductionService(gemini_client=gemini)


def get_ordering_service(gemini: Gemini = Depends(get_gemini_client)) -> OrderingService:
    return OrderingService(gemini_client=gemini)


def get_chance_loss_service() -> ChanceLossService:
    return ChanceLossService()


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> bool:
    if not settings.AI_SERVICE_TOKEN:
        if settings.APP_ENV != "local":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI_SERVICE_TOKEN이 설정되지 않았습니다. 서버 환경변수를 확인하세요.",
            )
        # 로컬 개발 모드: 토큰 미설정 + Bearer 미사용 요청만 허용
        if credentials is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="AI_SERVICE_TOKEN이 설정되지 않았습니다. 서버 환경변수를 확인하세요.",
            )
        warnings.warn("AI_SERVICE_TOKEN 미설정 — 인증 없이 통과 (로컬 개발 전용).", stacklevel=1)
        return True

    try:
        if credentials is None or credentials.credentials != settings.AI_SERVICE_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or missing service token",
            )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        logger.error(f"인증 검증 중 예상치 못한 오류: {exc}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authentication failed",
        )
    return True
