import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_token, get_production_service, get_ordering_service
from api.schemas import (
    ProductionPredictRequest, ProductionPredictResponse,
    OrderingRecommendRequest, OrderingRecommendResponse
)
from services.production_service import ProductionService
from services.ordering_service import OrderingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/management", tags=["management"])


@router.post("/production/predict", response_model=ProductionPredictResponse, dependencies=[Depends(verify_token)])
async def predict_production(
    payload: ProductionPredictRequest,
    service: ProductionService = Depends(get_production_service)
) -> ProductionPredictResponse:
    try:
        logger.info("생산 위험 예측 요청: %s", payload.sku)
        result = await asyncio.to_thread(service.predict_stock, payload)
        return result
    except Exception as exc:
        logger.exception("생산 예측 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="생산 예측에 실패했습니다.",
        ) from exc


@router.post("/ordering/recommend", response_model=OrderingRecommendResponse, dependencies=[Depends(verify_token)])
async def recommend_ordering(
    payload: OrderingRecommendRequest,
    service: OrderingService = Depends(get_ordering_service)
) -> OrderingRecommendResponse:
    try:
        logger.info("주문 추천 요청: 매장 %s", payload.store_id)
        result = await asyncio.to_thread(service.recommend_ordering, payload)
        return result
    except Exception as exc:
        logger.exception("주문 추천 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="주문 추천에 실패했습니다.",
        ) from exc
