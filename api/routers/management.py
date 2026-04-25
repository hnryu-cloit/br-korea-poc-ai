import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from api.dependencies import (
    get_chance_loss_service,
    get_ml_predict_service,
    get_ordering_service,
    get_production_service,
    verify_token,
)
from api.error_contract import build_error_detail
from schemas.contracts import (
    DeadlineAlertBatchRequest,
    DeadlineAlertBatchResponse,
    DeadlineAlertResponse,
    ExceptionCheckRequest,
    ExceptionCheckResult,
    FeedbackCorrectionResponse,
    FeedbackRecord,
    OrderingRecommendationRequest,
    OrderingRecommendationResponse,
    PushNotificationListResponse,
    SimulationFullRequest,
    SimulationReportResponse,
)
from schemas.management import (
    OrderingOption,
    OrderingRecommendRequest,
    OrderingRecommendResponse,
    ProductionPredictRequest,
    ProductionPredictResponse,
)
from services.chance_loss_service import ChanceLossService
from services.ml_predict_service import MLPredictService
from services.ordering_service import OrderingService
from services.production_service import ProductionService, normalize_payload_df

router = APIRouter(tags=["management"])
logger = logging.getLogger(__name__)


def _raise_internal_error(
    *,
    request: Request,
    exc: Exception,
    log_message: str,
    error_code: str,
    message: str,
    retryable: bool,
) -> None:
    logger.exception(log_message)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=build_error_detail(
            request,
            error_code=error_code,
            message=message,
            retryable=retryable,
        ),
    ) from exc


class ChanceLossRequest(BaseModel):
    store_id: str
    item_id: str
    target_date: str
    unit_price: float = 1500.0


@router.post(
    "/api/production/chance-loss",
    dependencies=[Depends(verify_token)],
    summary="[QA 테스트용] 찬스로스(기회손실) 정량 추정 엔진 단독 실행",
    description="AI-COMMON-035 QA 검증을 위해 ChanceLossEngine을 단독으로 실행하고 결과를 반환합니다.",
)
async def estimate_chance_loss(
    payload: ChanceLossRequest,
    request: Request,
    service: ChanceLossService = Depends(get_chance_loss_service),
):
    """찬스로스 추정 결과를 반환합니다."""
    try:
        return await asyncio.to_thread(
            service.estimate_from_db,
            store_id=payload.store_id,
            item_id=payload.item_id,
            target_date=payload.target_date,
            unit_price=payload.unit_price,
        )
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="찬스로스 추정 중 오류 발생",
            error_code="CHANCE_LOSS_FAILED",
            message="찬스로스 추정에 실패했습니다.",
            retryable=True,
        )

@router.post(
    "/api/production/simulation",
    response_model=SimulationReportResponse,
    dependencies=[Depends(verify_token)],
)
async def get_production_simulation(
    payload: SimulationFullRequest,
    request: Request,
    service: ProductionService = Depends(get_production_service),
) -> SimulationReportResponse:
    """
    [FE/BE 연동] 과거 데이터를 기반으로 AI 생산 가이드 시뮬레이션 리포트를 생성합니다.
    백엔드가 DB에서 조회한 inventory/production/sales 데이터를 포함해 전달합니다.
    """
    try:
        logger.info(f"시뮬레이션 요청: 매장 {payload.store_id}, 상품 {payload.item_id}")

        inv_df = normalize_payload_df(payload.inventory_data)
        prod_df = normalize_payload_df(payload.production_data)
        sales_df = normalize_payload_df(payload.sales_data)

        result = await asyncio.to_thread(
            service.get_simulation_report, payload, inv_df, prod_df, sales_df
        )
        return result
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="시뮬레이션 생성 중 오류 발생",
            error_code="PRODUCTION_SIMULATION_FAILED",
            message="시뮬레이션 생성에 실패했습니다.",
            retryable=True,
        )


@router.post(
    "/management/production/predict",
    response_model=ProductionPredictResponse,
    dependencies=[Depends(verify_token)],
)
async def predict_production(
    payload: ProductionPredictRequest,
    request: Request,
    service: ProductionService = Depends(get_production_service),
) -> ProductionPredictResponse:
    try:
        logger.info("생산 예측 요청: SKU %s", payload.sku)
        result = await asyncio.to_thread(service.predict_stock, payload)
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=build_error_detail(
                request,
                error_code="PRODUCTION_PREDICT_INVALID_INPUT",
                message=str(exc),
                retryable=False,
            ),
        ) from exc
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="생산 예측 중 오류 발생",
            error_code="PRODUCTION_PREDICT_FAILED",
            message="생산 예측에 실패했습니다.",
            retryable=True,
        )


@router.post(
    "/management/ordering/recommend",
    response_model=OrderingRecommendResponse,
    dependencies=[Depends(verify_token)],
)
async def recommend_ordering_compat(
    payload: OrderingRecommendRequest,
    request: Request,
    service: OrderingService = Depends(get_ordering_service),
) -> OrderingRecommendResponse:
    try:
        logger.info("주문 추천 요청: 매장 %s", payload.store_id)
        contract_payload = OrderingRecommendationRequest(
            store_id=payload.store_id,
            target_date=payload.current_date,
            current_context={
                **(payload.current_context or {}),
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
                recommended_qty=option.recommended_qty,
                priority=index,
                option_id=option.option_id,
                title=option.title,
                basis=option.basis,
                description=option.description,
                recommended=bool(option.recommended),
                reasoning_text=option.reasoning_text or option.reasoning,
                reasoning_metrics=option.reasoning_metrics,
                special_factors=option.special_factors,
                seasonality_weight=option.seasonality_weight,
                items=option.items,
            )
            for index, option in enumerate(result.recommendations, start=1)
        ]
        return OrderingRecommendResponse(
            options=options,
            reasoning=result.summary_insight,
            deadline_minutes=result.deadline_minutes,
            deadline_at=result.deadline_at,
            purpose_text=result.purpose_text,
            caution_text=result.caution_text,
            weather_summary=result.weather_summary,
            trend_summary=result.trend_summary,
            business_date=result.business_date,
            guardrail_note=result.caution_text
            or OrderingRecommendResponse.model_fields["guardrail_note"].default,
        )
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="주문 추천 중 오류 발생",
            error_code="ORDERING_RECOMMEND_FAILED",
            message="주문 추천에 실패했습니다.",
            retryable=True,
        )


@router.post(
    "/ordering/recommend",
    response_model=OrderingRecommendationResponse,
    dependencies=[Depends(verify_token)],
)
async def recommend_ordering(
    payload: OrderingRecommendationRequest,
    request: Request,
    service: OrderingService = Depends(get_ordering_service),
) -> OrderingRecommendationResponse:
    try:
        logger.info("주문 추천 요청: 매장 %s", payload.store_id)
        result = await asyncio.to_thread(service.recommend_ordering, payload)
        return result
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="주문 추천 중 오류 발생",
            error_code="ORDERING_RECOMMEND_FAILED",
            message="주문 추천에 실패했습니다.",
            retryable=True,
        )


@router.post(
    "/api/production/feedback",
    response_model=FeedbackCorrectionResponse,
    dependencies=[Depends(verify_token)],
)
async def submit_production_feedback(
    payload: FeedbackRecord,
    request: Request,
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
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="피드백 처리 중 오류",
            error_code="PRODUCTION_FEEDBACK_FAILED",
            message="피드백 처리에 실패했습니다.",
            retryable=False,
        )


@router.post(
    "/api/production/check-exceptions",
    response_model=ExceptionCheckResult,
    dependencies=[Depends(verify_token)],
)
async def check_production_exception_rules(
    payload: ExceptionCheckRequest,
    request: Request,
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
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="예외 규칙 확인 중 오류",
            error_code="PRODUCTION_EXCEPTION_RULE_FAILED",
            message="예외 규칙 확인에 실패했습니다.",
            retryable=False,
        )


@router.get(
    "/api/production/alerts/push",
    response_model=PushNotificationListResponse,
    dependencies=[Depends(verify_token)],
)
async def get_production_push_alerts(
    request: Request,
    store_id: str = Query(..., description="매장 ID"),
    service: ProductionService = Depends(get_production_service),
) -> PushNotificationListResponse:
    """백엔드 폴링용 생산 PUSH 알림 페이로드 목록을 반환합니다."""
    try:
        return service.get_push_notification_payloads(store_id=store_id)
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="PUSH 알림 조회 중 오류",
            error_code="PRODUCTION_PUSH_ALERTS_FAILED",
            message="PUSH 알림 조회에 실패했습니다.",
            retryable=True,
        )


@router.get(
    "/api/ordering/deadline-alerts",
    response_model=DeadlineAlertResponse,
    dependencies=[Depends(verify_token)],
)
async def get_ordering_deadline_alerts(
    request: Request,
    store_id: str = Query(..., description="매장 ID"),
    service: OrderingService = Depends(get_ordering_service),
) -> DeadlineAlertResponse:
    """주문 마감까지 남은 시간과 알림 여부를 반환합니다 (기본 마감: 14:00 KST)."""
    try:
        return await asyncio.to_thread(service.get_deadline_alerts, store_id)
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="마감 알림 조회 중 오류",
            error_code="ORDERING_DEADLINE_ALERT_FAILED",
            message="마감 알림 조회에 실패했습니다.",
            retryable=True,
        )


@router.post(
    "/api/ordering/deadline-alerts/batch",
    response_model=DeadlineAlertBatchResponse,
    dependencies=[Depends(verify_token)],
)
async def get_ordering_deadline_alerts_batch(
    payload: DeadlineAlertBatchRequest,
    request: Request,
    service: OrderingService = Depends(get_ordering_service),
) -> DeadlineAlertBatchResponse:
    """여러 매장의 주문 마감 알림 정보를 일괄 조회합니다."""
    store_ids = [store_id.strip() for store_id in payload.store_ids if store_id and store_id.strip()]
    store_ids = list(dict.fromkeys(store_ids))
    if not store_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_error_detail(
                request,
                error_code="ORDERING_DEADLINE_ALERT_BATCH_INVALID",
                message="store_ids는 1개 이상 필요합니다.",
                retryable=False,
            ),
        )

    try:
        tasks = [asyncio.to_thread(service.get_deadline_alerts, store_id) for store_id in store_ids]
        items = await asyncio.gather(*tasks)
        return DeadlineAlertBatchResponse(items=items)
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="마감 알림 배치 조회 중 오류",
            error_code="ORDERING_DEADLINE_ALERT_BATCH_FAILED",
            message="마감 알림 배치 조회에 실패했습니다.",
            retryable=True,
        )


# ---------------------------------------------------------------------------
# ML 모델 형식 호환 엔드포인트
# 입력: {store_id, sku}  출력: {prediction_result: {...}}
# ---------------------------------------------------------------------------

class MLPredictRequest(BaseModel):
    store_id: str
    sku: str

@router.post(
    "/predict",
    dependencies=[Depends(verify_token)],
    summary="ML 모델 형식 생산 예측",
    description="store_id + sku만으로 DB 데이터 기반 1시간 후 재고를 예측합니다.",
)
async def predict_ml_format(
    payload: MLPredictRequest,
    request: Request,
    service: MLPredictService = Depends(get_ml_predict_service),
) -> dict:
    """ML 모델 I/O 형식으로 1시간 후 재고를 예측합니다."""
    try:
        result = await asyncio.to_thread(service.predict, payload.store_id, payload.sku)
        if not result:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=build_error_detail(
                    request,
                    error_code="PREDICT_NO_DATA",
                    message=f"store_id={payload.store_id}, sku={payload.sku} 에 해당하는 재고 데이터가 없습니다.",
                    retryable=False,
                ),
            )

        return result
    except HTTPException:
        raise
    except Exception as exc:
        _raise_internal_error(
            request=request,
            exc=exc,
            log_message="ML 형식 예측 중 오류",
            error_code="PREDICT_FAILED",
            message="예측에 실패했습니다.",
            retryable=True,
        )
