from __future__ import annotations

import logging
import warnings

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings, get_settings

logger = logging.getLogger(__name__)

from common.gemini import Gemini

from services.sales_analyzer import SalesAnalyzer
from services.channel_payment_analyzer import ChannelPaymentAnalyzer
from services.production_service import ProductionService
from services.ordering_service import OrderingService
from services.rag_service import RAGService
from services.orchestrator import AgentOrchestrator

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

def get_channel_payment_analyzer(gemini: Gemini = Depends(get_gemini_client)) -> ChannelPaymentAnalyzer:
    return ChannelPaymentAnalyzer(gemini_client=gemini)

def get_sales_service(gemini: Gemini = Depends(get_gemini_client)) -> SalesAnalyzer:
    return SalesAnalyzer(gemini_client=gemini)

def get_production_service(gemini: Gemini = Depends(get_gemini_client)) -> ProductionService:
    return ProductionService(gemini_client=gemini)

def get_ordering_service(gemini: Gemini = Depends(get_gemini_client)) -> OrderingService:
    return OrderingService(gemini_client=gemini)

async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> bool:
    if not settings.AI_SERVICE_TOKEN:
        # 토큰 미설정 — Bearer 헤더가 없는 요청은 통과 (로컬 개발용)
        # Bearer 헤더가 있으면 검증 설정이 없으므로 거부
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

