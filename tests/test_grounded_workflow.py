from __future__ import annotations

import json
import sys
import types

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


class _FakeGemini:
    def __init__(self) -> None:
        self.calls = 0

    def call_gemini_text(self, prompt: str, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({
                "intent": "어제 생산 상위 품목 확인",
                "relevant_tables": ["PROD_DTL"],
            }, ensure_ascii=False)
        if self.calls == 2:
            return json.dumps({
                "sql": 'SELECT "ITEM_NM", SUM(COALESCE("PROD_QTY",0)) AS "TOTAL_PROD" FROM "PROD_DTL" WHERE "MASKED_STOR_CD" = :store_id GROUP BY "ITEM_NM"',
                "description": "생산 수량 집계",
                "relevant_tables": ["PROD_DTL"],
            }, ensure_ascii=False)
        return json.dumps({
            "text": "어제 생산량 기준으로 A 상품이 가장 높았습니다.",
            "evidence": ["A 상품 생산량 15개", "B 상품 생산량 10개"],
            "actions": ["상위 품목 재고 확인"],
        }, ensure_ascii=False)


class _InconsistentAnswerGemini:
    def __init__(self) -> None:
        self.calls = 0

    def call_gemini_text(self, prompt: str, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return json.dumps({
                "intent": "상위 품목 조회",
                "relevant_tables": ["PROD_DTL"],
            }, ensure_ascii=False)
        if self.calls == 2:
            return json.dumps({
                "sql": 'SELECT "ITEM_NM", SUM(COALESCE("PROD_QTY",0)) AS "TOTAL_PROD" FROM "PROD_DTL" WHERE "MASKED_STOR_CD" = :store_id GROUP BY "ITEM_NM" ORDER BY "TOTAL_PROD" DESC LIMIT 2',
                "description": "생산 수량 상위 집계",
                "relevant_tables": ["PROD_DTL"],
            }, ensure_ascii=False)
        return json.dumps({
            "text": "1위는 A 15개, 2위는 B 10개입니다.",
            "evidence": ["A 15", "B 10"],
            "actions": ["상위 품목 확인"],
        }, ensure_ascii=False)


def test_grounded_workflow_keeps_trace_metadata(monkeypatch):
    workflow = GroundedWorkflow(_FakeGemini())

    def _fake_run(self, sql, store_id, agent_name=None, target_tables=None, params=None):
        return ([{"ITEM_NM": "A", "TOTAL_PROD": 15}, {"ITEM_NM": "B", "TOTAL_PROD": 10}], ["ITEM_NM", "TOTAL_PROD"])

    monkeypatch.setattr("services.grounded_workflow.QueryExecutor.run", _fake_run)

    result = workflow.run(query="어제 생산량 상위 품목 알려줘", store_id="POC_001", domain="production")

    assert result["keywords"]
    assert result["intent"] == "어제 생산 상위 품목 확인"
    assert result["relevant_tables"] == ["PROD_DTL"]
    assert result["sql"].startswith("SELECT")
    assert result["row_count"] == 2


def test_grounded_workflow_falls_back_when_llm_adds_unexpected_numbers(monkeypatch):
    workflow = GroundedWorkflow(_InconsistentAnswerGemini())

    def _fake_run(self, sql, store_id, agent_name=None, target_tables=None, params=None):
        return ([{"ITEM_NM": "A", "TOTAL_PROD": 15}, {"ITEM_NM": "B", "TOTAL_PROD": 10}], ["ITEM_NM", "TOTAL_PROD"])

    monkeypatch.setattr("services.grounded_workflow.QueryExecutor.run", _fake_run)

    result = workflow.run(query="생산량 상위 2개 품목 알려줘", store_id="POC_001", domain="production")

    assert result["text"] == "생산량 상위 2개 품목 알려줘 조회 결과는 A | 15, B | 10입니다."


def test_build_fallback_text_formats_hour_columns():
    text = _build_fallback_text(
        "어제 매출 피크 시간대 몇 시야",
        [{"tmzon_div": "17", "revenue": 74600}],
    )

    assert text == "어제 매출 피크 시간대 몇 시야 조회 결과는 17시 | 74,600입니다."
