import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_token
from schemas.generation import GenerationResponse, SalesQueryRequest
from pipeline.run import run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])


@router.post("", response_model=GenerationResponse, dependencies=[Depends(verify_token)])
async def generate(payload: SalesQueryRequest) -> GenerationResponse:
    """에이전트 파이프라인을 실행하고 구조화된 생성 결과를 반환합니다."""
    try:
        logger.info("파이프라인 실행 시작 (프롬프트: %s)", payload.prompt[:30])
        result = await run_pipeline(payload.prompt)
        logger.info("파이프라인 실행 완료")
        return GenerationResponse(status="ok", result=result)
    except Exception as exc:
        logger.exception("파이프라인 실행 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="파이프라인 실행에 실패했습니다.",
        ) from exc
