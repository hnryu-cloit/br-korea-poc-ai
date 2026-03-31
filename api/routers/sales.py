import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_token, get_sales_analyzer
from api.schemas import SalesQueryRequest, SalesQueryResponse
from services.sales_analyzer import SalesAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("/query", response_model=SalesQueryResponse, dependencies=[Depends(verify_token)])
async def query_sales(
    payload: SalesQueryRequest, 
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer)
) -> SalesQueryResponse:
    try:
        logger.info("매출 분석 요청: %s", payload.prompt[:50])
        result = await asyncio.to_thread(analyzer.analyze, payload.prompt)
        return result
    except Exception as exc:
        logger.exception("매출 분석 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="매출 분석에 실패했습니다.",
        ) from exc
