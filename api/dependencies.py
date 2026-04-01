from __future__ import annotations

import warnings

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings, get_settings

from common.gemini import Gemini
from services.sales_analyzer import SalesAnalyzer
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

def get_production_service(gemini: Gemini = Depends(get_gemini_client)) -> ProductionService:
    return ProductionService(gemini_client=gemini)

def get_ordering_service(gemini: Gemini = Depends(get_gemini_client)) -> OrderingService:
    return OrderingService(gemini_client=gemini)

async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> bool:
    if not settings.AI_SERVICE_TOKEN:
        warnings.warn("AI_SERVICE_TOKEN 미설정 — 토큰 검증을 건너뜁니다.", stacklevel=1)
        return True
    if credentials is None or credentials.credentials != settings.AI_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing service token",
        )
    return True