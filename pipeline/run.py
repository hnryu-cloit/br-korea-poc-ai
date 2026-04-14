from __future__ import annotations
from typing import Any, Dict

from common.logger import init_logger
from common.gemini import Gemini
from services.orchestrator import AgentOrchestrator

logger = init_logger("pipeline")


async def run_pipeline(prompt: str, context: Dict[str, Any] | None = None) -> dict[str, object]:
    """
    AI 파이프라인 메인 진입점 — 자연어 입력을 오케스트레이터에 위임해 결과를 반환한다.
    """
    logger.info("파이프라인 실행 시작: %s", prompt[:50])
    
    gemini = Gemini()
    orchestrator = AgentOrchestrator(gemini)
    
    # 오케스트레이터에 자연어 처리 위임
    result = await orchestrator.handle_request(prompt, context)

    # 응답 직렬화: Pydantic 모델 → dict → 문자열 순으로 변환
    if hasattr(result, "model_dump"):
        return result.model_dump()
    elif isinstance(result, dict):
        return result
    else:
        return {"text": str(result)}
