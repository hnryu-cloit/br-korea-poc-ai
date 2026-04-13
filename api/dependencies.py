from __future__ import annotations

import warnings

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.config import Settings, get_settings

from common.gemini import Gemini

try:
    from services.sales_analyzer import SalesAnalyzer
except ImportError:  # pragma: no cover - fallback for trimmed POC snapshot
    class SalesAnalyzer:  # type: ignore[too-many-ancestors]
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def analyze(self, *args: object, **kwargs: object) -> object:
            raise NotImplementedError

try:
    from services.channel_payment_analyzer import ChannelPaymentAnalyzer
except ImportError:  # pragma: no cover - fallback for trimmed POC snapshot
    class ChannelPaymentAnalyzer:  # type: ignore[too-many-ancestors]
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def analyze(self, *args: object, **kwargs: object) -> object:
            raise NotImplementedError

from services.production_service import ProductionService
from services.ordering_service import OrderingService

try:
    from services.rag_service import RAGService
except ImportError:  # pragma: no cover - fallback for trimmed POC snapshot
    class RAGService:  # type: ignore[too-many-ancestors]
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def generate_with_rag(self, *args: object, **kwargs: object) -> object:
            raise NotImplementedError

try:
    from services.orchestrator import AgentOrchestrator
except ImportError:  # pragma: no cover - fallback for trimmed POC snapshot
    class AgentOrchestrator:  # type: ignore[too-many-ancestors]
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def handle_request(self, *args: object, **kwargs: object) -> object:
            raise NotImplementedError

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
        warnings.warn("AI_SERVICE_TOKEN 미설정 — 토큰 검증을 건너뜁니다.", stacklevel=1)
        return True
    if credentials is None or credentials.credentials != settings.AI_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing service token",
        )
    return True
