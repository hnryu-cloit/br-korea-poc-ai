"""
AI 서비스 엔드포인트 통합 테스트
- LLM(Gemini) 호출을 mock으로 대체하여 API 계약만 검증합니다.
- app.dependency_overrides를 사용해 FastAPI DI를 올바르게 오버라이드합니다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.config import get_settings, Settings
from api.dependencies import get_sales_analyzer, get_production_service, get_ordering_service
from api.main import app
from api.schemas import (
    OrderingRecommendResponse,
    ProductionPredictResponse,
    SalesQueryResponse,
)

TOKEN = "test-token"


def _settings_with_token():
    return Settings(AI_SERVICE_TOKEN=TOKEN)


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides[get_settings] = _settings_with_token
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def auth_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


# ── Health ────────────────────────────────────────────────────────────────────

def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# ── Sales Query ───────────────────────────────────────────────────────────────

def test_sales_query_requires_token(client: TestClient) -> None:
    res = client.post("/sales/query", json={"prompt": "배달 매출 분석해줘"})
    assert res.status_code == 403


def test_sales_query_success(client: TestClient) -> None:
    stub_response = SalesQueryResponse(
        text="이번 주 배달 매출은 전주 대비 14% 감소했습니다.",
        evidence=["배달앱 노출 순위 하락"],
        actions=["광고 입찰가 조정 권고"],
        confidence_score=0.92,
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = stub_response
    app.dependency_overrides[get_sales_analyzer] = lambda: mock_analyzer

    try:
        res = client.post(
            "/sales/query",
            json={"prompt": "배달 매출 분석해줘"},
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_sales_analyzer, None)

    assert res.status_code == 200
    body = res.json()
    assert body["text"] == stub_response.text
    assert isinstance(body["evidence"], list)
    assert isinstance(body["actions"], list)


def test_sales_query_invalid_payload(client: TestClient) -> None:
    res = client.post("/sales/query", json={}, headers=auth_headers())
    assert res.status_code == 422


# ── Production Predict ────────────────────────────────────────────────────────

def test_production_predict_success(client: TestClient) -> None:
    stub_response = ProductionPredictResponse(
        sku="SKU_001",
        predicted_stock_1h=12.0,
        risk_detected=True,
        stockout_expected_at="14:30",
        alert_message="1시간 이내 품절 위험. 지금 생산을 시작하세요.",
        confidence=0.88,
    )

    mock_service = MagicMock()
    mock_service.predict_stock.return_value = stub_response
    app.dependency_overrides[get_production_service] = lambda: mock_service

    try:
        res = client.post(
            "/management/production/predict",
            json={
                "sku": "SKU_001",
                "current_stock": 15,
                "history": [{"timestamp": "2024-01-01T12:00:00", "stock": 20, "production": 0, "sales": 5}],
                "pattern_4w": [0.8, 1.2],
            },
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_production_service, None)

    assert res.status_code == 200
    body = res.json()
    assert body["sku"] == "SKU_001"
    assert body["risk_detected"] is True
    assert "alert_message" in body
    assert body["confidence"] == pytest.approx(0.88)


def test_production_predict_requires_token(client: TestClient) -> None:
    res = client.post(
        "/management/production/predict",
        json={"sku": "X", "current_stock": 0, "history": [], "pattern_4w": []},
    )
    assert res.status_code == 403


# ── Ordering Recommend ────────────────────────────────────────────────────────

def test_ordering_recommend_success(client: TestClient) -> None:
    stub_response = OrderingRecommendResponse(
        options=[
            {"name": "전주 동요일 기준", "recommended_quantity": 120, "priority": 1},
            {"name": "전전주 동요일 기준", "recommended_quantity": 110, "priority": 2},
            {"name": "전월 동요일 기준", "recommended_quantity": 105, "priority": 3},
        ],
        reasoning="지난주 동요일 수요가 가장 최신 패턴과 유사합니다.",
    )

    mock_service = MagicMock()
    mock_service.recommend_ordering.return_value = stub_response
    app.dependency_overrides[get_ordering_service] = lambda: mock_service

    try:
        res = client.post(
            "/management/ordering/recommend",
            json={"store_id": "POC_001", "current_date": "2024-01-15"},
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_ordering_service, None)

    assert res.status_code == 200
    body = res.json()
    assert len(body["options"]) == 3
    assert body["options"][0]["priority"] == 1
    assert "reasoning" in body
    assert "guardrail_note" in body


def test_ordering_recommend_requires_token(client: TestClient) -> None:
    res = client.post(
        "/management/ordering/recommend",
        json={"store_id": "POC_001", "current_date": "2024-01-15"},
    )
    assert res.status_code == 403