import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_token
from pipeline.run import run_pipeline
from schemas.generation import GenerationResponse, SalesQueryRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generation", tags=["generation"])


@router.post("", response_model=GenerationResponse, dependencies=[Depends(verify_token)])
async def generate(payload: SalesQueryRequest) -> GenerationResponse:
    """에이전트 파이프라인을 실행하고 구조화된 생성 결과를 반환합니다."""
    try:
        logger.info("파이프라인 실행 시작 (프롬프트: %s)", payload.prompt[:30])
        pipeline_context = dict(payload.context or {})
        if payload.store_id:
            pipeline_context["store_id"] = payload.store_id

        result = await run_pipeline(payload.prompt, context=pipeline_context or None)
        logger.info("파이프라인 실행 완료")
        return GenerationResponse(status="ok", result=result)
    except (ValueError, TypeError, RuntimeError) as exc:
        logger.exception("파이프라인 실행 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="파이프라인 실행에 실패했습니다.",
        ) from exc
