from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProductionPredictRequest(BaseModel):
    sku: str
    store_id: str | None = None
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
    lower_bound: float | None = None
    upper_bound: float | None = None
    confidence_level: str | None = None


class OrderingRecommendRequest(BaseModel):
    store_id: str
    current_date: str
    is_campaign: bool = False
    is_holiday: bool = False
    current_context: dict[str, Any] = Field(default_factory=dict)


class OrderingOption(BaseModel):
    name: str
    recommended_qty: int
    priority: int
    option_id: str | None = None
    title: str | None = None
    basis: str | None = None
    description: str | None = None
    recommended: bool = False
    reasoning_text: str | None = None
    reasoning_metrics: list[dict[str, str]] = Field(default_factory=list)
    special_factors: list[str] = Field(default_factory=list)
    seasonality_weight: float | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


class OrderingRecommendResponse(BaseModel):
    options: list[OrderingOption]
    reasoning: str
    deadline_minutes: int | None = None
    deadline_at: str | None = None
    purpose_text: str | None = None
    caution_text: str | None = None
    weather_summary: str | None = None
    trend_summary: str | None = None
    business_date: str | None = None
    guardrail_note: str = (
        "최종 주문 결정은 점주의 권한입니다. 추천 옵션은 보조 자료로만 활용해주세요."
    )
