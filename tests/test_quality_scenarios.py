"""AI 서비스 품질 시나리오 테스트 (10개 이상).

RFP 요구 시나리오 기준 비즈니스 로직, 보안, 응답 구조를 검증합니다.
외부 DB / Gemini API / PIL 호출 없이 실행 가능합니다.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── PIL / Gemini 모듈 스텁 (설치 없이 테스트 실행) ───────────────────────
if "PIL" not in sys.modules:
    _pil_stub = types.ModuleType("PIL")
    _pil_stub.Image = MagicMock()  # type: ignore[attr-defined]
    sys.modules["PIL"] = _pil_stub
    sys.modules["PIL.Image"] = _pil_stub.Image

# common.gemini 스텁 — Gemini 클라이언트 클래스만 노출
if "common.gemini" not in sys.modules:
    _gemini_stub = types.ModuleType("common.gemini")

    class _FakeGemini:  # noqa: D401
        def call_gemini_text(self, *a, **kw):
            return {}

    _gemini_stub.Gemini = _FakeGemini  # type: ignore[attr-defined]
    sys.modules["common.gemini"] = _gemini_stub

# common.logger 스텁 — colorlog 없어도 동작
if "colorlog" not in sys.modules:
    _colorlog_stub = types.ModuleType("colorlog")
    _colorlog_stub.ColoredFormatter = MagicMock()  # type: ignore[attr-defined]
    sys.modules["colorlog"] = _colorlog_stub

if "common.logger" not in sys.modules:
    _logger_stub = types.ModuleType("common.logger")
    import logging as _logging

    def _init_logger(name: str):
        return _logging.getLogger(name)

    _logger_stub.init_logger = _init_logger  # type: ignore[attr-defined]
    sys.modules["common.logger"] = _logger_stub


# ── Scenario 1: 데이터 추출 엔진 - 총 매출 질의 ──────────────────────────
def test_data_extraction_total_sales_intent():
    from services.data_extraction_engine import DataExtractionEngine

    engine = DataExtractionEngine()
    result = engine.extract("총 매출이 얼마야?", store_id="gangnam")
    assert result["intent"] == "total_sales"
    assert "answer" in result and len(result["answer"]) > 0
    assert result["data"]["total_revenue"] > 0


# ── Scenario 2: 데이터 추출 엔진 - 피크 시간 질의 ────────────────────────
def test_data_extraction_peak_hours_intent():
    from services.data_extraction_engine import DataExtractionEngine

    engine = DataExtractionEngine()
    result = engine.extract("가장 바쁜 시간대가 언제야?", store_id="gangnam")
    assert result["intent"] == "peak_hours"
    assert "peak_start" in result["data"]
    assert "peak_revenue_ratio" in result["data"]


# ── Scenario 3: 데이터 추출 엔진 - 인기 메뉴 질의 ────────────────────────
def test_data_extraction_top_items_intent():
    from services.data_extraction_engine import DataExtractionEngine

    engine = DataExtractionEngine()
    result = engine.extract("인기 메뉴가 뭐야?", store_id="gangnam")
    assert result["intent"] == "top_items"
    assert "items" in result["data"]
    assert len(result["data"]["items"]) > 0


# ── Scenario 4: 데이터 추출 엔진 - 수익성 질의 표준 마진 ──────────────────
def test_data_extraction_profitability_standard_margin():
    from services.data_extraction_engine import DataExtractionEngine

    engine = DataExtractionEngine()
    result = engine.extract("이번 달 이익이 얼마야?", store_id="gangnam")
    assert result["intent"] == "profitability"
    assert result["data"]["margin_rate"] == 0.65


# ── Scenario 5: 데이터 추출 엔진 - 일반 fallback ─────────────────────────
def test_data_extraction_general_fallback():
    from services.data_extraction_engine import DataExtractionEngine

    engine = DataExtractionEngine()
    result = engine.extract("날씨가 어때?", store_id="gangnam")
    assert result["intent"] == "general"
    assert "answer" in result


# ── Scenario 5-1: 질의 분류기 - PII 마스킹 + 민감 질의 분류 ──────────────────
def test_query_classifier_masks_pii_and_marks_sensitive():
    from services.query_classifier import QueryClassifier

    classifier = QueryClassifier()
    result = classifier.classify_details("010-1234-5678 번호로 연락줘. 순이익은 얼마야?")
    assert result["query_type"] == "SENSITIVE"
    assert "phone_number" in result["masked_fields"]
    assert "***-****-****" in result["masked_query"]


# ── Scenario 5-2: RAG 서비스 - 기본 지식 베이스 fallback ─────────────────────
def test_rag_service_returns_text_and_sources_without_external_docs():
    # test_api_integration.py가 stub을 sys.modules에 등록할 수 있으므로 제거 후 실제 모듈 로드
    import sys as _sys

    _sys.modules.pop("services.rag_service", None)
    from services.rag_service import RAGService

    class _FakeGemini:
        def call_gemini_text(self, *args, **kwargs):
            return "운영 가이드 기반 답변입니다."

    service = RAGService(gemini_client=_FakeGemini(), knowledge_base=None)
    result = service.generate_with_rag("주문 마감 전에 무엇을 확인해야 해?")

    assert "text" in result
    assert result["text"]
    assert isinstance(result["sources"], list)


# ── Scenario 6: 피드백 보정 - EMA 계수 갱신 ──────────────────────────────
def test_production_feedback_correction_ema():
    from unittest.mock import MagicMock

    from services.production_service import ProductionService

    service = ProductionService(gemini_client=MagicMock())
    # 처음 피드백: 추천 100 → 실제 120 → ratio 1.2
    result = service.apply_feedback_correction("store1", "sku-A", 100.0, 120.0)
    # EMA: 0.3 * 1.2 + 0.7 * 1.0 = 1.06
    assert abs(result.correction_factor - 1.06) < 0.01
    assert result.store_id == "store1"


# ── Scenario 7: 피드백 보정 - 누적 적용 ──────────────────────────────────
def test_production_feedback_correction_accumulates():
    from unittest.mock import MagicMock

    from services.production_service import ProductionService

    service = ProductionService(gemini_client=MagicMock())
    service.apply_feedback_correction("store1", "sku-A", 100.0, 120.0)
    corrected = service.get_corrected_prediction("store1", "sku-A", 100.0)
    assert corrected > 100.0  # 보정 계수 > 1이므로 예측값 증가


# ── Scenario 8: 예외 룰셋 - 마감 30분 이내 억제 ──────────────────────────
def test_production_exception_suppressed_near_closing():
    from unittest.mock import MagicMock

    from services.production_service import ProductionService

    service = ProductionService(gemini_client=MagicMock())
    result = service.check_production_exceptions(
        sku_id="sku-A",
        recommended_qty=50.0,
        store_closing_time="22:00",
        current_time="21:45",  # 마감 15분 전
    )
    assert result.suppressed is True
    assert "마감" in (result.reason or "")


# ── Scenario 9: 예외 룰셋 - 대량 주문 수동 검토 ──────────────────────────
def test_production_exception_large_order_manual_review():
    from unittest.mock import MagicMock

    from services.production_service import ProductionService

    service = ProductionService(gemini_client=MagicMock())
    result = service.check_production_exceptions(
        sku_id="sku-A",
        recommended_qty=400.0,
        store_closing_time="22:00",
        current_time="14:00",  # 마감 8시간 전 - 억제 없음
        avg_production_qty=100.0,  # 400 > 3 * 100
    )
    assert result.suppressed is False
    assert result.requires_manual_review is True


# ── Scenario 10: rate limiter - 허용 범위 내 통과 ────────────────────────
def test_rate_limiter_allows_within_limit():
    from common.rate_limiter import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(max_calls=5, window_seconds=60)
    for _ in range(5):
        assert limiter.is_allowed("test") is True


# ── Scenario 11: rate limiter - 한도 초과 시 차단 ────────────────────────
def test_rate_limiter_blocks_over_limit():
    from common.rate_limiter import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(max_calls=3, window_seconds=60)
    for _ in range(3):
        limiter.is_allowed("test")
    assert limiter.is_allowed("test") is False


# ── Scenario 12: rate limiter - 남은 횟수 감소 ───────────────────────────
def test_rate_limiter_remaining_decreases():
    from common.rate_limiter import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(max_calls=10, window_seconds=60)
    assert limiter.get_remaining("key") == 10
    limiter.is_allowed("key")
    assert limiter.get_remaining("key") == 9


# ── Scenario 13: LLM 로거 - 전화번호 마스킹 ──────────────────────────────
def test_llm_logger_masks_phone_number():
    from common.llm_logger import LLMCallLogger

    inst = LLMCallLogger()
    masked = inst.mask_sensitive("연락처: 010-1234-5678")
    assert "010-1234-5678" not in masked
    assert "[PHONE]" in masked


# ── Scenario 14: LLM 로거 - 이메일 마스킹 ───────────────────────────────
def test_llm_logger_masks_email():
    from common.llm_logger import LLMCallLogger

    inst = LLMCallLogger()
    masked = inst.mask_sensitive("이메일: user@example.com 로 연락해주세요")
    assert "user@example.com" not in masked
    assert "[EMAIL]" in masked


# ── Scenario 15: 주문 마감 알림 - 마감 통과 후 ───────────────────────────
def test_ordering_deadline_alert_passed():
    from services.ordering_service import OrderingService

    service = OrderingService(gemini_client=MagicMock())
    # 마감 시각을 0:00으로 설정하면 항상 passed 상태
    result = service.get_deadline_alerts("gangnam", deadline_hour=0, deadline_minute=0)
    assert result.alert_level == "passed"
    assert result.should_alert is False
    assert result.notification_id == 2001
    assert result.target_path == "/ordering"


# ── Scenario 16: 주문 마감 알림 - 마감 전 상태 ───────────────────────────
def test_ordering_deadline_alert_future():
    from services.ordering_service import OrderingService

    service = OrderingService(gemini_client=MagicMock())
    # 마감 시각을 23:59으로 설정하면 항상 남은 시간이 있음
    result = service.get_deadline_alerts("gangnam", deadline_hour=23, deadline_minute=59)
    assert result.alert_level in ("urgent", "normal")
    assert result.minutes_remaining >= 0
    assert result.title is not None
    assert result.target_roles == ["store_owner"]


# ── Scenario 17: InventoryPredictor 메타 미로드 시 예측 차단 ────────────────
def test_inventory_predictor_meta_not_loaded_blocks_prediction():
    """model_meta.joblib 없이 로드된 predictor는 predict_next_hour_sales에서 RuntimeError를 발생시킨다."""
    import pandas as pd

    # joblib.load 스텁 — 모델만 반환, 메타 파일은 존재하지 않도록
    stub_model = MagicMock()
    stub_model.predict = MagicMock(return_value=[5.0])

    with patch("os.path.exists") as mock_exists, patch("joblib.load", return_value=stub_model):
        # model 파일은 존재, meta 파일은 없음
        mock_exists.side_effect = lambda path: "inventory_lgbm_model" in path

        from services.inventory_predictor import InventoryPredictor

        predictor = InventoryPredictor.__new__(InventoryPredictor)
        predictor.model = None
        predictor.stats = {}
        predictor.meta_loaded = False
        predictor.model_dir = "/tmp/fake"
        predictor.model_path = "/tmp/fake/inventory_lgbm_model.pkl"
        predictor.meta_path = "/tmp/fake/model_meta.joblib"
        predictor.feature_cols = []

    # meta_loaded=False 인 상태에서 model이 있어도 예측 차단
    predictor.model = stub_model
    predictor.meta_loaded = False

    with pytest.raises(RuntimeError, match="모델 메타"):
        predictor.predict_next_hour_sales(
            store_cd="A001",
            item_cd="ITEM01",
            current_time=__import__("datetime").datetime(2024, 1, 15, 10, 0),
            history_df=pd.DataFrame(),
        )


# ── Scenario 18: InventoryPredictor 메타 구조 오류 시 meta_loaded=False ─────
def test_inventory_predictor_invalid_meta_structure_sets_meta_loaded_false():
    """meta가 dict이지만 'hist'·'store'·'item' 키가 없으면 meta_loaded가 False로 유지된다."""

    stub_model = MagicMock()
    bad_meta = {"unknown_key": "unexpected_value"}

    with patch("os.path.exists", return_value=True), patch("joblib.load") as mock_load:
        mock_load.side_effect = [stub_model, bad_meta]

        from services.inventory_predictor import InventoryPredictor

        predictor = InventoryPredictor.__new__(InventoryPredictor)
        predictor.model = None
        predictor.stats = {}
        predictor.meta_loaded = False
        predictor.model_dir = "/tmp/fake"
        predictor.model_path = "/tmp/fake/inventory_lgbm_model.pkl"
        predictor.meta_path = "/tmp/fake/model_meta.joblib"
        predictor.feature_cols = []
        predictor.load_model()

    assert predictor.meta_loaded is False
    assert predictor.stats == {}
