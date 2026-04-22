from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from evaluators.hallucination_detector import HallucinationDetector
from schemas.contracts import SalesQueryRequest, SalesQueryResponse


@dataclass
class GroundedQuestionCase:
    id: str
    domain: str
    store_id: str
    query: str
    expect_data: bool = True


def load_question_set(path: str | Path) -> list[GroundedQuestionCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [GroundedQuestionCase(**item) for item in raw]


def build_service(domain: str, gemini_client: Any) -> Any:
    if domain == "production":
        from services.production_service import ProductionService

        return ProductionService(gemini_client)
    if domain == "ordering":
        from services.ordering_service import OrderingService

        return OrderingService(gemini_client)
    if domain == "sales":
        from services.sales_analyzer import SalesAnalyzer

        return SalesAnalyzer(gemini_client)
    if domain == "channel":
        from services.channel_payment_analyzer import ChannelPaymentAnalyzer

        return ChannelPaymentAnalyzer(gemini_client)
    raise ValueError(f"Unsupported domain: {domain}")


def run_question_case(case: GroundedQuestionCase, gemini_client: Any) -> dict[str, Any]:
    service = build_service(case.domain, gemini_client)
    payload = SalesQueryRequest(store_id=case.store_id, query=case.query, domain=case.domain)
    response = service.analyze(payload)
    return normalize_response(response)


def normalize_response(response: Any) -> dict[str, Any]:
    if isinstance(response, SalesQueryResponse):
        normalized = response.model_dump()
        answer = normalized.get("answer", {})
        normalized["text"] = answer.get("text", "")
        normalized["evidence"] = answer.get("evidence", [])
        normalized["actions"] = answer.get("actions", [])
        normalized["sql"] = None
        normalized["relevant_tables"] = []
        for item in normalized.get("data_lineage", []):
            if item.get("agent") == "GroundedWorkflow":
                normalized["sql"] = item.get("query")
                normalized["relevant_tables"] = item.get("tables", [])
                normalized["keywords"] = item.get("keywords", [])
                normalized["intent"] = item.get("intent")
                normalized["row_count"] = item.get("row_count", 0)
                break
        return normalized
    if isinstance(response, dict):
        return response
    raise TypeError(f"Unsupported response type: {type(response)}")


def rerun_sql(sql: str, store_id: str, relevant_tables: list[str] | None = None) -> list[dict[str, Any]]:
    from services.sql_pipeline import QueryExecutor

    executor = QueryExecutor(os.getenv("DATABASE_URL"))
    rows, _ = executor.run(
        sql,
        store_id,
        agent_name="GroundedConsistencyVerifier",
        target_tables=relevant_tables or [],
        params={"store_id": store_id},
    )
    return rows


def compare_answer_numbers_to_rows(
    answer_text: str,
    rows: list[dict[str, Any]],
    query: str = "",
    *,
    tolerance: float = 0.05,
) -> dict[str, Any]:
    answer_numbers = _extract_numbers(answer_text)
    allowed_numbers = _extract_numbers(query)
    allowed_numbers.update(_numbers_from_rows(rows))
    answer_only = sorted(num for num in answer_numbers if not _contains_number(allowed_numbers, num, tolerance))
    return {
        "is_consistent": not answer_only,
        "answer_numbers": sorted(answer_numbers),
        "allowed_numbers": sorted(allowed_numbers),
        "unexpected_numbers": answer_only,
    }


def evaluate_with_optional_llm_judge(
    answer_text: str,
    rows: list[dict[str, Any]],
    gemini_client: Any,
) -> dict[str, Any] | None:
    if os.getenv("GROUNDING_LLM_JUDGE", "0") != "1":
        return None
    return asyncio.run(
        HallucinationDetector.evaluate_with_llm_judge(
            generated_text=answer_text,
            raw_data_context={"rows": rows[:30]},
            ai_client=gemini_client,
        )
    )


def _extract_numbers(text: str) -> set[float]:
    numbers: set[float] = set()
    for token in re.findall(r"\d+(?:\.\d+)?", text):
        value = float(token)
        if 20000101 <= value <= 20991231:
            continue
        numbers.add(value)
    return numbers


def _numbers_from_rows(rows: list[dict[str, Any]]) -> set[float]:
    numbers: set[float] = set()
    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, Decimal):
                value = float(value)
            if isinstance(value, (int, float)):
                number = float(value)
                numbers.add(number)
                if abs(number - round(number)) < 1e-9:
                    numbers.add(float(int(round(number))))
    return numbers


def _contains_number(pool: set[float], target: float, tolerance: float) -> bool:
    for number in pool:
        if abs(number - target) <= tolerance:
            return True
    return False
