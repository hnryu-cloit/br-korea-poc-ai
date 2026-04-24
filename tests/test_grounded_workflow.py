from __future__ import annotations

import json
from pathlib import Path
import sys
import types

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "google" not in sys.modules:
    google_module = types.ModuleType("google")
    google_module.__path__ = []  # type: ignore[attr-defined]
    sys.modules["google"] = google_module
else:
    google_module = sys.modules["google"]

if "google.genai" not in sys.modules:
    genai_module = types.ModuleType("google.genai")
    genai_module.__path__ = []  # type: ignore[attr-defined]
    genai_types = types.ModuleType("google.genai.types")
    genai_module.types = genai_types
    sys.modules["google.genai"] = genai_module
    sys.modules["google.genai.types"] = genai_types
    google_module.genai = genai_module

if "colorlog" not in sys.modules:
    colorlog_module = types.ModuleType("colorlog")

    class _DummyColoredFormatter:
        def __init__(self, *args, **kwargs):
            pass

    colorlog_module.ColoredFormatter = _DummyColoredFormatter
    sys.modules["colorlog"] = colorlog_module

from services.grounded_workflow import GroundedWorkflow, _build_fallback_text


class _NoopGoldenResolver:
    def resolve_and_execute(self, **kwargs):  # noqa: ANN003
        return None

    def suggest_follow_up_queries(self, **kwargs):  # noqa: ANN003
        return ["추가 질문 1", "추가 질문 2", "추가 질문 3"]

    def rank_candidates(self, *args, **kwargs):  # noqa: ANN003
        return []


class _HitGoldenResolver:
    def resolve_and_execute(self, **kwargs):  # noqa: ANN003
        return {
            "text": "골든쿼리 매칭 응답입니다.",
            "evidence": ["골든쿼리 매칭: 005-001- (score=0.93)"],
            "actions": ["재조회"],
            "intent": "golden query match: 005-001-",
            "relevant_tables": ["core_channel_sales"],
            "sql": "SELECT 1",
            "queried_period": {"date_from": "20260301", "date_to": "20260305"},
            "row_count": 1,
            "matched_query_id": "005-001-",
            "match_score": 0.93,
        }

    def suggest_follow_up_queries(self, **kwargs):  # noqa: ANN003
        return ["후속 질문 A", "후속 질문 B", "후속 질문 C"]

    def rank_candidates(self, *args, **kwargs):  # noqa: ANN003
        return []


class _FakeGemini:
    def __init__(self) -> None:
        self.calls = 0

    def call_gemini_text(self, prompt: str, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {
                    "intent": "어제 생산 상위 품목 확인",
                    "relevant_tables": ["PROD_DTL"],
                },
                ensure_ascii=False,
            )
        if self.calls == 2:
            return json.dumps(
                {
                    "sql": 'SELECT "ITEM_NM", SUM(COALESCE("PROD_QTY",0)) AS "TOTAL_PROD" FROM "PROD_DTL" WHERE "MASKED_STOR_CD" = :store_id GROUP BY "ITEM_NM"',
                    "description": "생산 수량 집계",
                    "relevant_tables": ["PROD_DTL"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "text": "어제 생산 기준으로 A 상품이 가장 많습니다.",
                "evidence": ["A 상품 생산량 15개", "B 상품 생산량 10개"],
                "actions": ["상위 품목 재고 확인"],
            },
            ensure_ascii=False,
        )


class _InconsistentAnswerGemini:
    def __init__(self) -> None:
        self.calls = 0

    def call_gemini_text(self, prompt: str, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {
                    "intent": "상위 품목 조회",
                    "relevant_tables": ["PROD_DTL"],
                },
                ensure_ascii=False,
            )
        if self.calls == 2:
            return json.dumps(
                {
                    "sql": 'SELECT "ITEM_NM", SUM(COALESCE("PROD_QTY",0)) AS "TOTAL_PROD" FROM "PROD_DTL" WHERE "MASKED_STOR_CD" = :store_id GROUP BY "ITEM_NM" ORDER BY "TOTAL_PROD" DESC LIMIT 2',
                    "description": "생산 수량 상위 집계",
                    "relevant_tables": ["PROD_DTL"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "text": "1위는 A 99개, 2위는 B 88개입니다.",
                "evidence": ["A 99", "B 88"],
                "actions": ["상위 품목 확인"],
            },
            ensure_ascii=False,
        )


class _OrderingPolicyGemini:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def call_gemini_text(self, prompt: str, *args, **kwargs):
        self.calls += 1
        self.prompts.append(prompt)
        if self.calls == 1:
            return json.dumps(
                {
                    "intent": "오늘 납품 예정으로 등록된 발주 수량 조회",
                    "relevant_tables": ["raw_order_extract"],
                },
                ensure_ascii=False,
            )
        if self.calls == 2:
            return json.dumps(
                {
                    "sql": "SELECT 4236 AS inbound_qty",
                    "description": "납품예정일 기준 발주 수량 조회",
                    "relevant_tables": ["raw_order_extract"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "text": "오늘 입고 예정 발주 수량은 4,236개입니다.",
                "evidence": ["dlv_dt 기준 4,236개"],
                "actions": ["품목별 상세 확인"],
            },
            ensure_ascii=False,
        )


def test_grounded_workflow_keeps_trace_metadata(monkeypatch):
    workflow = GroundedWorkflow(_FakeGemini())
    workflow.golden_resolver = _NoopGoldenResolver()

    def _fake_run(self, sql, store_id, agent_name=None, target_tables=None, params=None):
        return ([{"ITEM_NM": "A", "TOTAL_PROD": 15}, {"ITEM_NM": "B", "TOTAL_PROD": 10}], ["ITEM_NM", "TOTAL_PROD"])

    monkeypatch.setattr("services.grounded_workflow.QueryExecutor.run", _fake_run)

    result = workflow.run(query="어제 생산 상위 품목 알려줘", store_id="POC_001", domain="production")

    assert result["keywords"]
    assert result["intent"] == "어제 생산 상위 품목 확인"
    assert result["relevant_tables"] == ["PROD_DTL"]
    assert result["sql"].startswith("SELECT")
    assert result["row_count"] == 2


def test_grounded_workflow_falls_back_when_llm_adds_unexpected_numbers(monkeypatch):
    workflow = GroundedWorkflow(_InconsistentAnswerGemini())
    workflow.golden_resolver = _NoopGoldenResolver()

    def _fake_run(self, sql, store_id, agent_name=None, target_tables=None, params=None):
        return ([{"ITEM_NM": "A", "TOTAL_PROD": 15}, {"ITEM_NM": "B", "TOTAL_PROD": 10}], ["ITEM_NM", "TOTAL_PROD"])

    monkeypatch.setattr("services.grounded_workflow.QueryExecutor.run", _fake_run)

    result = workflow.run(query="생산 상위 2개 품목 알려줘", store_id="POC_001", domain="production")

    assert result["text"] == "생산 상위 2개 품목 알려줘 조회 결과는 A | 15, B | 10입니다."


def test_build_fallback_text_formats_hour_columns():
    text = _build_fallback_text(
        "어제 매출 피크 시간대가 몇 시야",
        [{"tmzon_div": "17", "revenue": 74600}],
    )

    assert text == "어제 매출 피크 시간대가 몇 시야 조회 결과는 17시 | 74,600입니다."


def test_ordering_policy_rewrites_inbound_question(monkeypatch):
    gemini = _OrderingPolicyGemini()
    workflow = GroundedWorkflow(gemini)
    workflow.golden_resolver = _NoopGoldenResolver()

    def _fake_run(self, sql, store_id, agent_name=None, target_tables=None, params=None):
        return ([{"inbound_qty": 4236}], ["inbound_qty"])

    monkeypatch.setattr("services.grounded_workflow.QueryExecutor.run", _fake_run)

    result = workflow.run(query="오늘 입고 예정 발주 수량 몇 개야?", store_id="POC_001", domain="ordering")

    assert "오늘 납품 예정으로 등록된 발주 수량" in gemini.prompts[0]
    assert result["text"].startswith("질문이 애매할 수 있어 '오늘 납품 예정으로 등록된 발주 수량' 기준으로 안내드립니다.")
    assert result["evidence"][0] == "입고 예정 질의는 납품예정일(dlv_dt) 기준으로 해석했습니다."


def test_ordering_policy_blocks_order_date_question():
    workflow = GroundedWorkflow(_FakeGemini())
    workflow.golden_resolver = _NoopGoldenResolver()

    result = workflow.run(query="오늘 발주한 수량 몇 개야?", store_id="POC_001", domain="ordering")

    assert result["processing_route"] == "ordering_policy_guard"
    assert result["sql"] is None
    assert "발주일 정보가 없어" in result["text"]


def test_grounded_workflow_returns_golden_query_hit():
    workflow = GroundedWorkflow(_FakeGemini())
    workflow.golden_resolver = _HitGoldenResolver()

    result = workflow.run(query="오늘 시간대 매출 보여줘", store_id="POC_001", domain="sales")

    assert result["processing_route"] == "golden_query_hit"
    assert result["matched_query_id"] == "005-001-"
    assert result["match_score"] == 0.93


def test_grounded_workflow_blocks_when_golden_query_only_and_no_match():
    workflow = GroundedWorkflow(_FakeGemini())
    workflow.golden_resolver = _NoopGoldenResolver()

    result = workflow.run(
        query="아무 관련 없는 자유 질문",
        store_id="POC_001",
        domain="sales",
        golden_query_only=True,
    )

    assert result["processing_route"] == "golden_query_miss_block"
    assert "죄송합니다. 현재 문의주신 답변" in result["text"]
