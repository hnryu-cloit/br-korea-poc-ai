import asyncio
import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_ordering_service, get_production_service, verify_token
from schemas.management import (
    OrderingRecommendRequest,
    OrderingRecommendResponse,
    OrderingOption,
    ProductionPredictRequest,
    ProductionPredictResponse,
)
from schemas.contracts import (
    OrderingRecommendationRequest,
    OrderingRecommendationResponse,
    SimulationFullRequest,
    SimulationReportResponse,
    FeedbackRecord,
    FeedbackCorrectionResponse,
    ExceptionCheckRequest,
    ExceptionCheckResult,
    PushNotificationListResponse,
    DeadlineAlertResponse,
)
from services.ordering_service import OrderingService
from services.production_service import ProductionService

router = APIRouter(tags=["management"])
logger = logging.getLogger(__name__)


@router.post(
    "/api/production/simulation",
    response_model=SimulationReportResponse,
    dependencies=[Depends(verify_token)],
)
async def get_production_simulation(
    payload: SimulationFullRequest,
    service: ProductionService = Depends(get_production_service),
) -> SimulationReportResponse:
    """
    [FE/BE 연동] 과거 데이터를 기반으로 AI 생산 가이드 시뮬레이션 리포트를 생성합니다.
    백엔드가 DB에서 조회한 inventory/production/sales 데이터를 포함해 전달합니다.
    """
    try:
        logger.info(f"시뮬레이션 요청: 매장 {payload.store_id}, 상품 {payload.item_id}")

        inv_df = pd.DataFrame(payload.inventory_data)
        prod_df = pd.DataFrame(payload.production_data)
        sales_df = pd.DataFrame(payload.sales_data)

        result = await asyncio.to_thread(
            service.get_simulation_report,
            payload, inv_df, prod_df, sales_df
        )
        return result
    except Exception as exc:
        logger.exception("시뮬레이션 생성 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"시뮬레이션 생성 실패: {str(exc)}",
        ) from exc


@router.post(
    "/management/production/predict",
    response_model=ProductionPredictResponse,
    dependencies=[Depends(verify_token)],
)
async def predict_production(
    payload: ProductionPredictRequest,
    service: ProductionService = Depends(get_production_service),
) -> ProductionPredictResponse:
    try:
        logger.info("생산 예측 요청: SKU %s", payload.sku)
        result = await asyncio.to_thread(
            service.predict_stock,
            payload.sku,
            payload.current_stock,
            payload.history,
            payload.pattern_4w,
        )
        return result
    except Exception as exc:
        logger.exception("생산 예측 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="생산 예측에 실패했습니다.",
        ) from exc


@router.post(
    "/management/ordering/recommend",
    response_model=OrderingRecommendResponse,
    dependencies=[Depends(verify_token)],
)
async def recommend_ordering_compat(
    payload: OrderingRecommendRequest,
    service: OrderingService = Depends(get_ordering_service),
) -> OrderingRecommendResponse:
    try:
        logger.info("주문 추천 요청: 매장 %s", payload.store_id)
        contract_payload = OrderingRecommendationRequest(
            store_id=payload.store_id,
            target_date=payload.current_date,
            current_context={
                "is_campaign": payload.is_campaign,
                "is_holiday": payload.is_holiday,
            },
            recent_stock_trends=[],
        )
        result = await asyncio.to_thread(
            service.recommend_ordering,
            contract_payload,
        )
        if isinstance(result, OrderingRecommendResponse):
            return result
        options = [
            OrderingOption(
                name=option.option_type.value,
                recommended_quantity=option.recommended_qty,
                priority=index,
            )
            for index, option in enumerate(result.recommendations, start=1)
        ]
        return OrderingRecommendResponse(options=options, reasoning=result.summary_insight)
    except Exception as exc:
        logger.exception("주문 추천 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="주문 추천에 실패했습니다.",
        ) from exc


@router.post(
    "/ordering/recommend",
    response_model=OrderingRecommendationResponse,
    dependencies=[Depends(verify_token)],
)
async def recommend_ordering(
    payload: OrderingRecommendationRequest,
    service: OrderingService = Depends(get_ordering_service),
) -> OrderingRecommendationResponse:
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


@router.post(
    "/api/production/feedback",
    response_model=FeedbackCorrectionResponse,
    dependencies=[Depends(verify_token)],
)
async def submit_production_feedback(
    payload: FeedbackRecord,
    service: ProductionService = Depends(get_production_service),
) -> FeedbackCorrectionResponse:
    """점주 실제 생산량을 피드백으로 등록해 예측 보정 계수를 갱신합니다."""
    try:
        return service.apply_feedback_correction(
            store_id=payload.store_id,
            sku_id=payload.sku_id,
            recommended_qty=payload.recommended_qty,
            actual_qty=payload.actual_qty,
        )
    except Exception as exc:
        logger.exception("피드백 처리 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.post(
    "/api/production/check-exceptions",
    response_model=ExceptionCheckResult,
    dependencies=[Depends(verify_token)],
)
async def check_production_exception_rules(
    payload: ExceptionCheckRequest,
    service: ProductionService = Depends(get_production_service),
) -> ExceptionCheckResult:
    """마감 직전 억제 및 대량 주문 수동 검토 예외 규칙을 확인합니다."""
    try:
        return service.check_production_exceptions(
            sku_id=payload.sku_id,
            recommended_qty=payload.recommended_qty,
            store_closing_time=payload.store_closing_time,
            current_time=payload.current_time,
            avg_production_qty=payload.avg_production_qty,
        )
    except Exception as exc:
        logger.exception("예외 규칙 확인 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/api/production/alerts/push",
    response_model=PushNotificationListResponse,
    dependencies=[Depends(verify_token)],
)
async def get_production_push_alerts(
    store_id: str = Query(..., description="매장 ID"),
    service: ProductionService = Depends(get_production_service),
) -> PushNotificationListResponse:
    """백엔드 폴링용 생산 PUSH 알림 페이로드 목록을 반환합니다."""
    try:
        return service.get_push_notification_payloads(store_id=store_id)
    except Exception as exc:
        logger.exception("PUSH 알림 조회 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/api/ordering/deadline-alerts",
    response_model=DeadlineAlertResponse,
    dependencies=[Depends(verify_token)],
)
async def get_ordering_deadline_alerts(
    store_id: str = Query(..., description="매장 ID"),
    service: OrderingService = Depends(get_ordering_service),
) -> DeadlineAlertResponse:
    """주문 마감까지 남은 시간과 알림 여부를 반환합니다 (기본 마감: 14:00 KST)."""
    try:
        return await asyncio.to_thread(service.get_deadline_alerts, store_id)
    except Exception as exc:
        logger.exception("마감 알림 조회 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
