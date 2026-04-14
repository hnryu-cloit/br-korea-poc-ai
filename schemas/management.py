from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ProductionPredictRequest(BaseModel):
    sku: str
    current_stock: int
    history: list[dict[str, Any]]
    pattern_4w: list[float]


class ProductionPredictResponse(BaseModel):
    sku: str
    predicted_stock_1h: float
    risk_detected: bool
    stockout_expected_at: str | None
    alert_message: str
    confidence: float


class OrderingRecommendRequest(BaseModel):
    store_id: str
    current_date: str
    is_campaign: bool = False
    is_holiday: bool = False


class OrderingOption(BaseModel):
    name: str
    recommended_quantity: int
    priority: int


class OrderingRecommendResponse(BaseModel):
    options: list[OrderingOption]
    reasoning: str
    guardrail_note: str = "최종 주문 결정은 점주의 권한입니다. 추천 옵션은 보조 자료로만 활용해주세요."
