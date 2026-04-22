"""
AI 서비스 엔드포인트 통합 테스트
- LLM(Gemini) 호출을 mock으로 대체하여 API 계약만 검증합니다.
- app.dependency_overrides를 사용해 FastAPI DI를 올바르게 오버라이드합니다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

AI_ROOT = Path(__file__).resolve().parents[1]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

if "PIL" not in sys.modules:
    pil_module = types.ModuleType("PIL")
    pil_module.__path__ = []  # type: ignore[attr-defined]
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = type("Image", (), {})
    pil_module.Image = pil_image_module
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_image_module

if "dotenv" not in sys.modules:
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    dotenv_module.dotenv_values = lambda *args, **kwargs: {}  # type: ignore[attr-defined]
    sys.modules["dotenv"] = dotenv_module

if "google" not in sys.modules:
    google_module = types.ModuleType("google")
    google_module.__path__ = []  # type: ignore[attr-defined]
    sys.modules["google"] = google_module
else:
    google_module = sys.modules["google"]

if "google.genai" not in sys.modules:
    genai_module = types.ModuleType("google.genai")
    genai_module.__path__ = []  # type: ignore[attr-defined]

    class _DummyModels:
        def embed_content(self, **_: object) -> object:
            return types.SimpleNamespace(embeddings=[types.SimpleNamespace(values=[])])

        def generate_content(self, **_: object) -> object:
            return types.SimpleNamespace(
                candidates=[
                    types.SimpleNamespace(
                        content=types.SimpleNamespace(parts=[types.SimpleNamespace(text="{}")]),
                    )
                ]
            )

    class _DummyFiles:
        def upload(self, **_: object) -> object:
            return types.SimpleNamespace()

    class _DummyClient:
        def __init__(self, api_key: str | None = None) -> None:
            self.api_key = api_key
            self.models = _DummyModels()
            self.files = _DummyFiles()

    genai_types = types.ModuleType("google.genai.types")

    class _DummyPart:
        @classmethod
        def from_bytes(cls, data: bytes, mime_type: str) -> dict[str, object]:
            return {"data": data, "mime_type": mime_type}

        @classmethod
        def from_text(cls, text: str) -> dict[str, str]:
            return {"text": text}

    class _DummyContent:
        def __init__(self, role: str, parts: list[object]) -> None:
            self.role = role
            self.parts = parts

    genai_types.Part = _DummyPart
    genai_types.Content = _DummyContent
    genai_module.Client = _DummyClient
    genai_module.types = genai_types
    sys.modules["google.genai"] = genai_module
    sys.modules["google.genai.types"] = genai_types
    google_module.genai = genai_module

if "colorlog" not in sys.modules:
    colorlog_module = types.ModuleType("colorlog")

    class _DummyColoredFormatter:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def format(self, record: object) -> str:
            return str(record)

    colorlog_module.ColoredFormatter = _DummyColoredFormatter
    sys.modules["colorlog"] = colorlog_module

for module_name, class_name in [
    ("services.sales_analyzer", "SalesAnalyzer"),
    ("services.channel_payment_analyzer", "ChannelPaymentAnalyzer"),
    ("services.rag_service", "RAGService"),
    ("services.orchestrator", "AgentOrchestrator"),
]:
    if module_name not in sys.modules:
        module = types.ModuleType(module_name)

        class _StubService:  # type: ignore[too-many-ancestors]
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

        setattr(module, class_name, _StubService)
        sys.modules[module_name] = module

from api.config import Settings, get_settings
from api.dependencies import (
    get_ordering_history_insight_service,
    get_ordering_service,
    get_production_service,
    get_sales_analyzer,
)
from api.main import app
from schemas.contracts import (
    ChartDataPoint,
    OrderingOption,
    OrderingRecommendationResponse,
    OrderOptionType,
    SalesInsight,
    SalesQueryResponse,
    SimulationReportResponse,
    SimulationSummary,
)
from schemas.management import ProductionPredictResponse

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


def test_core_routes_are_exposed(client: TestClient) -> None:
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/generation" in paths
    assert "/sales/query" in paths
    assert "/sales/query/channel-payment" in paths


# ── Sales Query ───────────────────────────────────────────────────────────────


def test_sales_query_requires_token(client: TestClient) -> None:
    res = client.post("/sales/query", json={"prompt": "배달 매출 분석해줘"})
    assert res.status_code == 403


def test_sales_query_success(client: TestClient) -> None:
    stub_response = SalesQueryResponse(
        answer=SalesInsight(
            text="이번 주 배달 매출은 전주 대비 14% 감소했습니다.",
            evidence=["배달앱 노출 순위 하락"],
            actions=["광고 입찰가 조정 권고"],
        ),
        source_data_period="최근 4주",
    )

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = stub_response
    app.dependency_overrides[get_sales_analyzer] = lambda: mock_analyzer

    try:
        res = client.post(
            "/sales/query",
            json={"store_id": "POC_001", "query": "배달 매출 분석해줘", "raw_data_context": []},
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_sales_analyzer, None)

    assert res.status_code == 200
    body = res.json()
    assert body["answer"]["text"] == stub_response.answer.text
    assert isinstance(body["answer"]["evidence"], list)
    assert isinstance(body["answer"]["actions"], list)


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
                "history": [
                    {"timestamp": "2024-01-01T12:00:00", "stock": 20, "production": 0, "sales": 5}
                ],
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
    # 계약 필드 전체 검증
    assert "predicted_stock_1h" in body
    assert "stockout_expected_at" in body
    assert "lower_bound" in body
    assert "upper_bound" in body
    assert "confidence_level" in body


def test_production_predict_requires_token(client: TestClient) -> None:
    res = client.post(
        "/management/production/predict",
        json={"sku": "X", "current_stock": 0, "history": [], "pattern_4w": []},
    )
    assert res.status_code == 403


# ── Ordering Recommend ────────────────────────────────────────────────────────


def test_ordering_recommend_success(client: TestClient) -> None:
    stub_response = OrderingRecommendationResponse(
        store_id="POC_001",
        recommendations=[
            OrderingOption(
                option_type=OrderOptionType.LAST_WEEK,
                recommended_qty=120,
                reasoning="지난주 동요일 수요가 가장 최신 패턴과 유사합니다.",
                expected_sales=120,
            ),
            OrderingOption(
                option_type=OrderOptionType.TWO_WEEKS_AGO,
                recommended_qty=110,
                reasoning="전전주 동요일 수요가 보조 기준으로 적합합니다.",
                expected_sales=110,
            ),
            OrderingOption(
                option_type=OrderOptionType.LAST_MONTH,
                recommended_qty=105,
                reasoning="전월 동요일 수요를 보완 지표로 사용합니다.",
                expected_sales=105,
            ),
        ],
        summary_insight="지난주 동요일 수요가 가장 최신 패턴과 유사합니다.",
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
    assert "deadline_minutes" in body
    assert "deadline_at" in body
    assert "business_date" in body
    assert "reasoning_text" in body["options"][0]
    assert "reasoning_metrics" in body["options"][0]
    assert "special_factors" in body["options"][0]
    assert "reasoning" in body
    assert "guardrail_note" in body


def test_ordering_recommend_requires_token(client: TestClient) -> None:
    res = client.post(
        "/management/ordering/recommend",
        json={"store_id": "POC_001", "current_date": "2024-01-15"},
    )
    assert res.status_code == 403


def test_ordering_recommend_current_contract_success(client: TestClient) -> None:
    stub_response = OrderingRecommendationResponse(
        store_id="POC_001",
        recommendations=[
            OrderingOption(
                option_type=OrderOptionType.LAST_WEEK,
                recommended_qty=120,
                reasoning="최근 패턴 기준으로 전주 동요일이 가장 유사합니다.",
                expected_sales=120,
            ),
            OrderingOption(
                option_type=OrderOptionType.TWO_WEEKS_AGO,
                recommended_qty=110,
                reasoning="전전주 동요일 수요가 보조 기준으로 적합합니다.",
                expected_sales=110,
            ),
            OrderingOption(
                option_type=OrderOptionType.LAST_MONTH,
                recommended_qty=105,
                reasoning="전월 동요일 수요를 보완 지표로 사용합니다.",
                expected_sales=105,
            ),
        ],
        summary_insight="최근 패턴 기준으로 전주 동요일이 가장 유사합니다.",
    )

    mock_service = MagicMock()
    mock_service.recommend_ordering.return_value = stub_response
    app.dependency_overrides[get_ordering_service] = lambda: mock_service

    try:
        res = client.post(
            "/ordering/recommend",
            json={
                "store_id": "POC_001",
                "target_date": "2024-01-15",
                "current_context": {"target_product": "초코 도넛"},
                "recent_stock_trends": [],
            },
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_ordering_service, None)

    assert res.status_code == 200
    body = res.json()
    assert body["store_id"] == "POC_001"
    assert len(body["recommendations"]) == 3
    assert body["summary_insight"].startswith("최근 패턴 기준")


def test_production_simulation_success(client: TestClient) -> None:
    stub_response = SimulationReportResponse(
        metadata={
            "store_id": "POC_001",
            "item_id": "SKU_001",
            "item_name": "초코 도넛",
            "date": "2024-01-15",
        },
        summary_metrics=SimulationSummary(
            additional_sales_qty=12.0,
            additional_profit_amt=18000,
            additional_waste_qty=2.0,
            additional_waste_cost=1400,
            net_profit_change=16600,
            performance_status="POSITIVE",
            chance_loss_reduction=4500.0,
        ),
        time_series_data=[
            ChartDataPoint(time="08:00", actual_stock=40.0, ai_guided_stock=52.0),
            ChartDataPoint(time="10:00", actual_stock=34.0, ai_guided_stock=46.0),
        ],
        actions_timeline=["[10:00] AI 추천으로 20개 추가 생산"],
    )

    mock_service = MagicMock()
    mock_service.get_simulation_report.return_value = stub_response
    app.dependency_overrides[get_production_service] = lambda: mock_service

    try:
        res = client.post(
            "/api/production/simulation",
            json={
                "store_id": "POC_001",
                "item_id": "SKU_001",
                "simulation_date": "2024-01-15",
                "lead_time_hour": 1,
                "margin_rate": 0.3,
                "inventory_data": [],
                "production_data": [],
                "sales_data": [],
            },
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_production_service, None)

    assert res.status_code == 200
    body = res.json()
    assert body["metadata"]["item_name"] == "초코 도넛"
    assert body["summary_metrics"]["net_profit_change"] == 16600
    assert len(body["time_series_data"]) == 2


def test_ordering_history_insights_success(client: TestClient) -> None:
    mock_service = MagicMock()
    mock_service.generate.return_value = {
        "kpis": [
            {"key": "auto_rate", "label": "자동 발주 비율", "value": "67.0%", "tone": "primary"},
            {"key": "manual_rate", "label": "수동 발주 비율", "value": "33.0%", "tone": "warning"},
        ],
        "anomalies": [
            {
                "id": "anomaly-1",
                "severity": "high",
                "kind": "확정 편차",
                "message": "주요 품목 확정량 편차가 큽니다.",
                "recommended_action": "전일 POS 추세를 반영해 발주량을 보정하세요.",
                "related_items": ["초코링"],
            }
        ],
        "top_changed_items": [
            {"item_nm": "초코링", "avg_ord_qty": 16.2, "latest_ord_qty": 28, "change_ratio": 0.7284}
        ],
        "sources": ["operations_guide:ordering", "ordering_history"],
        "retrieved_contexts": ["주문 마감 2시간 전 확정 편차 상위 품목 재점검"],
        "confidence": 0.86,
    }
    app.dependency_overrides[get_ordering_history_insight_service] = lambda: mock_service

    try:
        res = client.post(
            "/analytics/ordering/history/insights",
            json={
                "store_id": "POC_002",
                "filters": {"date_from": "2026-04-01", "date_to": "2026-04-22"},
                "history_items": [
                    {
                        "item_nm": "초코링",
                        "dlv_dt": "2026-04-22",
                        "ord_qty": 28,
                        "confrm_qty": 19,
                        "is_auto": False,
                    }
                ],
                "summary_stats": {"auto_rate": 0.67, "manual_rate": 0.33, "total_count": 21},
            },
            headers=auth_headers(),
        )
    finally:
        app.dependency_overrides.pop(get_ordering_history_insight_service, None)

    assert res.status_code == 200
    payload = res.json()
    assert len(payload["kpis"]) >= 1
    assert len(payload["anomalies"]) >= 1
    assert "trace_id" in payload
    assert payload["confidence"] == pytest.approx(0.86)


def test_ordering_history_insights_requires_token(client: TestClient) -> None:
    res = client.post(
        "/analytics/ordering/history/insights",
        json={"store_id": "POC_002", "filters": {}, "history_items": [], "summary_stats": {}},
    )
    assert res.status_code == 403
