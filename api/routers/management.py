import asyncio
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import pandas as pd
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from api.dependencies import get_chance_loss_service, get_ordering_service, get_production_service, verify_token
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
from services.inventory_predictor import InventoryPredictor
from services.ordering_service import OrderingService
from services.production_service import ProductionService, normalize_payload_df

router = APIRouter(tags=["management"])
logger = logging.getLogger(__name__)


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
        logger.exception("찬스로스 추정 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="CHANCE_LOSS_FAILED",
                message="찬스로스 추정에 실패했습니다.",
                retryable=True,
            ),
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
        logger.exception("시뮬레이션 생성 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="PRODUCTION_SIMULATION_FAILED",
                message="시뮬레이션 생성에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


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
        logger.exception("생산 예측 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="PRODUCTION_PREDICT_FAILED",
                message="생산 예측에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


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
        logger.exception("주문 추천 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="ORDERING_RECOMMEND_FAILED",
                message="주문 추천에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


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
        logger.exception("주문 추천 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="ORDERING_RECOMMEND_FAILED",
                message="주문 추천에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


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
        logger.exception("피드백 처리 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="PRODUCTION_FEEDBACK_FAILED",
                message="피드백 처리에 실패했습니다.",
                retryable=False,
            ),
        ) from exc


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
        logger.exception("예외 규칙 확인 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="PRODUCTION_EXCEPTION_RULE_FAILED",
                message="예외 규칙 확인에 실패했습니다.",
                retryable=False,
            ),
        ) from exc


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
        logger.exception("PUSH 알림 조회 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="PRODUCTION_PUSH_ALERTS_FAILED",
                message="PUSH 알림 조회에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


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
        logger.exception("마감 알림 조회 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="ORDERING_DEADLINE_ALERT_FAILED",
                message="마감 알림 조회에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


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
        logger.exception("마감 알림 배치 조회 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="ORDERING_DEADLINE_ALERT_BATCH_FAILED",
                message="마감 알림 배치 조회에 실패했습니다.",
                retryable=True,
            ),
        ) from exc


# ---------------------------------------------------------------------------
# ML 모델 형식 호환 엔드포인트
# 입력: {store_id, sku}  출력: {prediction_result: {...}}
# ---------------------------------------------------------------------------

class MLPredictRequest(BaseModel):
    store_id: str
    sku: str


def _get_db_engine():
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc",
    )
    return create_engine(db_url, pool_pre_ping=True)


def _fetch_stock_snapshot(store_id: str, sku: str) -> dict:
    """core_stock_rate에서 가장 최근 일자의 재고 스냅샷 조회."""
    engine = _get_db_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT prc_dt, ord_avg, sal_avg, stk_avg, stk_rt, is_stockout
                FROM core_stock_rate
                WHERE masked_stor_cd = :store_id
                  AND item_cd        = :sku
                ORDER BY prc_dt DESC
                LIMIT 1
            """),
            {"store_id": store_id, "sku": sku},
        ).fetchone()
    if row is None:
        return {}
    return dict(row._mapping)


def _fetch_recent_sales(store_id: str, sku: str, days: int = 7) -> list[dict]:
    """core_stock_rate에서 최근 N일 판매/재고 이력 조회."""
    engine = _get_db_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT prc_dt, sal_avg, stk_avg, ord_avg
                FROM core_stock_rate
                WHERE masked_stor_cd = :store_id
                  AND item_cd        = :sku
                ORDER BY prc_dt DESC
                LIMIT :days
            """),
            {"store_id": store_id, "sku": sku, "days": days},
        ).fetchall()
    return [dict(r._mapping) for r in rows]


def _build_predictor_history_df(store_id: str, sku: str, history_rows: list[dict]) -> pd.DataFrame:
    """InventoryPredictor 입력 형식으로 최근 판매 이력을 변환합니다."""
    rows: list[dict[str, object]] = []
    for row in history_rows:
        prc_dt = str(row.get("prc_dt") or "").strip()
        if len(prc_dt) != 8 or not prc_dt.isdigit():
            continue
        rows.append(
            {
                "MASKED_STOR_CD": store_id,
                "ITEM_CD": sku,
                "SALE_DT": prc_dt,
                "TMZON_DIV": 12,
                "SALE_QTY": float(row.get("sal_avg") or 0.0),
            }
        )
    return pd.DataFrame(rows)


@router.post(
    "/predict",
    dependencies=[Depends(verify_token)],
    summary="ML 모델 형식 생산 예측",
    description="store_id + sku만으로 DB 데이터 기반 1시간 후 재고를 예측합니다.",
)
async def predict_ml_format(
    payload: MLPredictRequest,
    request: Request,
) -> dict:
    """ML 모델 I/O 형식으로 1시간 후 재고를 예측합니다."""
    try:
        snapshot, history = await asyncio.gather(
            asyncio.to_thread(_fetch_stock_snapshot, payload.store_id, payload.sku),
            asyncio.to_thread(_fetch_recent_sales, payload.store_id, payload.sku, 7),
        )

        if not snapshot:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=build_error_detail(
                    request,
                    error_code="PREDICT_NO_DATA",
                    message=f"store_id={payload.store_id}, sku={payload.sku} 에 해당하는 재고 데이터가 없습니다.",
                    retryable=False,
                ),
            )

        current_stock = float(snapshot.get("stk_avg") or 0.0)
        predicted_sales_next_1h: float | None = None

        # 1) 학습 모델(InventoryPredictor) 우선 사용
        try:
            predictor = InventoryPredictor()
            history_df = _build_predictor_history_df(payload.store_id, payload.sku, history)
            predicted_sales_next_1h = float(
                predictor.predict_next_hour_sales(
                    payload.store_id,
                    payload.sku,
                    datetime.now(),
                    history_df,
                )
            )
            logger.info(
                "predict_ml_format: model prediction applied store_id=%s sku=%s sales_1h=%.2f",
                payload.store_id,
                payload.sku,
                predicted_sales_next_1h,
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.warning(
                "predict_ml_format: model prediction unavailable, fallback applied store_id=%s sku=%s error=%s",
                payload.store_id,
                payload.sku,
                exc,
            )

        # 2) 모델 실패/미로딩 시 기존 휴리스틱 폴백
        if predicted_sales_next_1h is None:
            recent_sales = [float(r.get("sal_avg") or 0.0) for r in history]
            avg_sales = sum(recent_sales) / len(recent_sales) if recent_sales else 0.0
            predicted_sales_next_1h = round(avg_sales / 8.0, 1)

        predicted_stock_after_1h = round(max(current_stock - predicted_sales_next_1h, 0.0), 1)
        risk_detected = predicted_stock_after_1h <= max(1.0, current_stock * 0.3)
        last_updated = snapshot.get("prc_dt", datetime.now().strftime("%Y%m%d"))
        if len(last_updated) == 8:
            last_updated = f"{last_updated[:4]}-{last_updated[4:6]}-{last_updated[6:]} 00:00"

        return {
            "prediction_result": {
                "store_id": payload.store_id,
                "sku": payload.sku,
                "current_status": {
                    "current_stock": current_stock,
                    "last_updated": last_updated,
                },
                "prediction": {
                    "predicted_sales_next_1h": predicted_sales_next_1h,
                    "predicted_stock_after_1h": predicted_stock_after_1h,
                    "risk_detected": risk_detected,
                },
            }
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ML 형식 예측 중 오류")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code="PREDICT_FAILED",
                message="예측에 실패했습니다.",
                retryable=True,
            ),
        ) from exc
