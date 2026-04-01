from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class GenerationResponse(BaseModel):
    status: str
    result: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


class SalesQueryRequest(BaseModel):
    prompt: str


class SalesQueryResponse(BaseModel):
    text: str
    evidence: list[str]
    actions: list[str]
    confidence_score: float | None = 1.0
    semantic_logic: str | None = None
    sources: list[str] | None = None
    # 시각화를 위한 구조화된 데이터 (차트 타입, 라벨, 수치 등)
    visual_data: dict[str, Any] | None = None 


class ProductionPredictRequest(BaseModel):
    sku: str
    current_stock: int
    history: list[dict[str, Any]]  # [timestamp, stock, production, sales]
    pattern_4w: list[float]  # [stock_trend, production_trend]


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
    name: str  # e.g., "전주 동요일 기준", "전전주 동요일 기준", "전월 동요일 기준"
    recommended_quantity: int
    priority: int


class OrderingRecommendResponse(BaseModel):
    options: list[OrderingOption]
    reasoning: str
    guardrail_note: str = "최종 주문 결정은 점주의 권한입니다. 추천 옵션은 보조 자료로만 활용해주세요."