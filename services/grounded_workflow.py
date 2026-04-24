from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from common.gemini import Gemini
from common.query_logger import query_logger
from services.query_classifier import QueryClassifier
from services.semantic_layer import SemanticLayer
from services.sql_pipeline import QueryExecutor, SQLGenerator, get_schema_context, get_table_hints

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "오늘",
    "어제",
    "최근",
    "이번",
    "지난",
    "기준",
    "조회",
    "보여줘",
    "알려줘",
    "무엇",
    "어때",
    "대비",
    "해주세요",
    "있어",
    "에서",
    "으로",
}

_DOMAIN_TO_QUERY_TYPE = {
    "sales": "sales",
    "production": "production",
    "ordering": "order",
    "channel": "channel",
}

_DOMAIN_TO_AGENT = {
    "sales": "SalesAnalyzer",
    "production": "ProductionService",
    "ordering": "OrderingService",
    "channel": "ChannelPaymentAnalyzer",
}


# JSON 직렬화 시 Decimal 값을 float으로 변환
def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported type: {type(value)}")


# 텍스트에서 숫자 토큰을 추출해 응답 일관성 검증에 사용
def _extract_numbers(text: str) -> set[float]:
    numbers: set[float] = set()
    for token in re.findall(r"\d+(?:\.\d+)?", str(text)):
        value = float(token)
        if 20000101 <= value <= 20991231:
            continue
        numbers.add(value)
    return numbers


# SQL 결과 행에 실제로 등장한 숫자 값 수집
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


def _contains_number(pool: set[float], target: float, tolerance: float = 0.05) -> bool:
    return any(abs(number - target) <= tolerance for number in pool)


def _format_number(value: float) -> str:
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return f"{int(round(rounded)):,}"
    return f"{rounded:,.1f}"


def _format_cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, (int, float)):
        numeric = float(value)
        if any(token in column.lower() for token in ("tmzon", "hour")) and abs(numeric - round(numeric)) < 1e-9:
            return f"{int(round(numeric))}시"
        return _format_number(numeric)

    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{1,2}", text) and any(token in column.lower() for token in ("tmzon", "hour")):
        return f"{int(text)}시"
    return text


# LLM 응답을 신뢰할 수 없을 때 결정론적 폴백 문장 생성
def _build_fallback_text(query: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "조회 결과가 없습니다."

    rendered_rows: list[str] = []
    for row in rows[:5]:
        parts: list[str] = []
        for column, value in row.items():
            formatted = _format_cell(column, value)
            if formatted:
                parts.append(formatted)
        if parts:
            rendered_rows.append(" | ".join(parts))

    if not rendered_rows:
        return "조회 결과가 없습니다."

    if len(rendered_rows) == 1:
        return f"{query} 조회 결과는 {rendered_rows[0]}입니다."
    return f"{query} 조회 결과는 {', '.join(rendered_rows)}입니다."


def _is_numeric_consistent(query: str, answer_text: str, rows: list[dict[str, Any]]) -> bool:
    if not answer_text.strip():
        return False
    allowed = _extract_numbers(query)
    allowed.update(_numbers_from_rows(rows))
    for number in _extract_numbers(answer_text):
        if not _contains_number(allowed, number):
            return False
    return True


# SQL 생성 전 모호한 순위 질의를 명확화
@dataclass(frozen=True)
class _OrderingQueryPolicy:
    query_for_sql: str
    answer_prefix: str = ""
    evidence_note: str | None = None
    unavailable_text: str | None = None
    unavailable_actions: tuple[str, ...] = ()


def _apply_ordering_query_policy(query: str) -> _OrderingQueryPolicy:
    compact = re.sub(r"\s+", "", query)
    has_inbound_schedule = any(token in compact for token in ("입고예정", "납품예정", "들어오기로된"))
    has_order_quantity = any(token in compact for token in ("발주수량", "발주물량", "주문수량"))
    asks_order_placed_today = any(
        token in compact
        for token in ("오늘발주한", "오늘넣은발주", "오늘주문한", "금일발주한", "방금발주한")
    )

    if asks_order_placed_today and not has_inbound_schedule:
        return _OrderingQueryPolicy(
            query_for_sql=query,
            unavailable_text=(
                "현재 데이터에는 발주일 정보가 없어 '오늘 발주한 수량'은 계산할 수 없습니다. "
                "대신 납품예정일 기준으로 잡힌 수량은 안내할 수 있습니다."
            ),
            unavailable_actions=(
                "'오늘 납품 예정으로 등록된 발주 수량'처럼 질문",
                "발주일 기준 데이터 적재 여부 확인",
            ),
        )

    if has_inbound_schedule and has_order_quantity:
        return _OrderingQueryPolicy(
            query_for_sql=(
                f"{query} "
                "(이 질문은 오늘 납품 예정으로 등록된 발주 수량으로 해석하고 "
                "납품예정일 dlv_dt 기준으로 조회한다)"
            ),
            answer_prefix=(
                "질문이 애매할 수 있어 '오늘 납품 예정으로 등록된 발주 수량' 기준으로 안내드립니다. "
            ),
            evidence_note="입고 예정 질의는 납품예정일(dlv_dt) 기준으로 해석했습니다.",
        )

    if has_inbound_schedule:
        return _OrderingQueryPolicy(
            query_for_sql=(
                f"{query} "
                "(입고 예정은 납품예정일 dlv_dt 기준으로 조회하고 오늘 발주 수량과는 구분한다)"
            ),
            evidence_note="입고 예정 질의는 납품예정일(dlv_dt) 기준으로 해석했습니다.",
        )

    return _OrderingQueryPolicy(query_for_sql=query)


class GroundedWorkflow:
    """LLM 기반 비즈니스 응답을 위한 공통 grounded 워크플로우"""

    def __init__(self, gemini: Gemini, db_url: str | None = None) -> None:
        self.gemini = gemini
        self.classifier = QueryClassifier()
        self.semantic_layer = SemanticLayer()
        self.sql_generator = SQLGenerator(gemini)
        self.executor = QueryExecutor(db_url)

    def run(
        self,
        *,
        query: str,
        store_id: str,
        domain: str,
        reference_date: str | None = None,
    ) -> dict[str, Any]:
        # Main grounded path:
        # classify -> choose tables -> generate SQL -> execute -> compose answer.
        details = self.classifier.classify_details(query)
        masked_query = str(details["masked_query"])
        if details["blocked"]:
            return {
                "text": "민감 정보가 포함된 질문으로 분류되어 직접 답변할 수 없습니다.",
                "keywords": [],
                "intent": "sensitive",
                "evidence": [f"masked_fields={details['masked_fields']}"],
                "actions": ["민감 정보를 제거하고 다시 질문", "운영 지침 기준으로 재문의"],
                "query_type": "SENSITIVE",
                "processing_route": "policy_block",
                "relevant_tables": [],
                "sql": None,
                "row_count": 0,
                "data_lineage": [],
                "masked_fields": details["masked_fields"],
            }

        ordering_policy = (
            _apply_ordering_query_policy(masked_query)
            if domain == "ordering"
            else _OrderingQueryPolicy(query_for_sql=masked_query)
        )
        if ordering_policy.unavailable_text:
            return {
                "text": ordering_policy.unavailable_text,
                "keywords": self.extract_keywords(masked_query),
                "intent": "ordering policy clarification",
                "evidence": ["발주일 기준 컬럼이 현재 raw_order_extract에 없습니다."],
                "actions": list(ordering_policy.unavailable_actions),
                "query_type": domain.upper(),
                "processing_route": "ordering_policy_guard",
                "relevant_tables": ["raw_order_extract"],
                "sql": None,
                "row_count": 0,
                "data_lineage": [],
                "masked_fields": details["masked_fields"],
            }

        effective_query = ordering_policy.query_for_sql
        keywords = self.extract_keywords(effective_query)
        intent, relevant_tables = self.analyze_intent(effective_query, keywords, domain)
        generated = self.sql_generator.generate(
            effective_query,
            store_id,
            query_type=_DOMAIN_TO_QUERY_TYPE.get(domain, "general"),
            table_hints_override=relevant_tables,
            intent_summary=intent,
            reference_date=reference_date,
        )
        rows, _ = self.executor.run(
            generated.sql,
            store_id,
            agent_name=_DOMAIN_TO_AGENT.get(domain, domain),
            target_tables=generated.relevant_tables,
            params={"store_id": store_id, "keywords": keywords, "intent": intent},
        )
        answer = self.compose_answer(
            query=effective_query,
            domain=domain,
            keywords=keywords,
            intent=intent,
            relevant_tables=generated.relevant_tables,
            sql=generated.sql,
            queried_period=generated.queried_period,
            rows=rows,
            answer_prefix=ordering_policy.answer_prefix,
            evidence_note=ordering_policy.evidence_note,
        )
        answer.update(
            {
                "keywords": keywords,
                "intent": intent,
                "relevant_tables": generated.relevant_tables,
                "sql": generated.sql,
                "queried_period": generated.queried_period,
                "row_count": len(rows),
                "query_type": domain.upper(),
                "processing_route": f"{domain}_grounded_workflow",
                "data_lineage": query_logger.get_history(_DOMAIN_TO_AGENT.get(domain, domain)),
                "masked_fields": details["masked_fields"],
            }
        )
        query_logger.clear_history()
        return answer

    def extract_keywords(self, query: str) -> list[str]:
        # Keeps compact domain keywords for trace and prompting.
        tokens = re.findall(r"[0-9A-Za-z_]+|[가-힣]{2,}", query)
        keywords: list[str] = []
        for token in tokens:
            normalized = token.strip()
            if not normalized or normalized in _STOPWORDS:
                continue
            if normalized not in keywords:
                keywords.append(normalized)
            if len(keywords) >= 8:
                break
        return keywords

    def analyze_intent(self, query: str, keywords: list[str], domain: str) -> tuple[str, list[str]]:
        # Combines rule-based business intent with LLM table selection.
        query_type = _DOMAIN_TO_QUERY_TYPE.get(domain, "general")
        schema_context = get_schema_context(get_table_hints(query_type))
        target_data_type, business_logic = self.semantic_layer.parse_query_intent(query)
        prompt = f"""
질문에서 사용된 키워드와 도메인 정보를 바탕으로 조회 의도와 필요한 테이블을 정리하세요.

[도메인]
{domain}

[질문]
{query}

[키워드]
{keywords}

[기본 힌트]
- semantic_type: {target_data_type}
- business_logic: {business_logic}

[사용 가능한 스키마]
{schema_context}

반드시 JSON으로만 답하세요.
{{
  "intent": "질문의 조회 의도를 한 문장으로 요약",
  "relevant_tables": ["table_a", "table_b"]
}}
"""
        try:
            raw = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(raw) if isinstance(raw, str) else raw
            intent = str(data.get("intent") or f"{domain} analysis")
            tables = [str(t) for t in data.get("relevant_tables", []) if str(t).strip()]
            if tables:
                return intent, tables
        except Exception as exc:
            logger.warning("intent analysis fallback: %s", exc)
        return f"{domain} analysis based on keywords: {', '.join(keywords) or query}", get_table_hints(query_type)

    def compose_answer(
        self,
        *,
        query: str,
        domain: str,
        keywords: list[str],
        intent: str,
        relevant_tables: list[str],
        sql: str,
        queried_period: dict[str, Any] | None,
        rows: list[dict[str, Any]],
        answer_prefix: str = "",
        evidence_note: str | None = None,
    ) -> dict[str, Any]:
        # Converts grounded rows into answer, evidence, and actions.
        serialized_rows = json.dumps(rows[:30], ensure_ascii=False, default=_json_default)
        prompt = f"""
당신은 매장 운영 데이터를 설명하는 분석가입니다.
아래 집계 결과만 근거로 자연어 답변을 만드세요.

[질문]
{query}

[핵심 키워드]
{keywords}

[분석 의도]
{intent}

[조회 테이블]
{relevant_tables}

[실행 SQL]
{sql}

[조회 기간]
{json.dumps(queried_period or {}, ensure_ascii=False)}

[조회 결과]
{serialized_rows}

규칙:
- 조회 결과에 없는 수치나 사실은 추가하지 마세요.
- 값이 없으면 데이터가 없다고 명확히 말하세요.
- queried_period가 별도 필드로 제공되므로 답변 본문에서 기간을 반복하지 마세요.
- 날짜나 기간 자체가 답변 핵심일 때만 본문에서 언급하세요.

반드시 JSON으로만 답하세요.
{{
  "text": "사용자에게 보여줄 자연어 답변",
  "evidence": ["근거 1", "근거 2"],
  "actions": ["후속 액션 1", "후속 액션 2"]
}}
"""
        try:
            raw = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(raw) if isinstance(raw, str) else raw
            answer = {
                "text": f"{answer_prefix}{data.get('text', '')}".strip(),
                "evidence": data.get("evidence", []),
                "actions": data.get("actions", []),
            }
            if evidence_note and evidence_note not in answer["evidence"]:
                answer["evidence"] = [evidence_note, *answer["evidence"]]
            if not _is_numeric_consistent(query, str(answer["text"]), rows):
                logger.warning("numeric consistency fallback triggered for query=%s", query)
                answer["text"] = f"{answer_prefix}{_build_fallback_text(query, rows)}".strip()
            return answer
        except Exception as exc:
            logger.error("answer composition failed: %s", exc)
            evidence = [f"조회 테이블: {', '.join(relevant_tables)}", f"조회 건수: {len(rows)}"]
            if evidence_note:
                evidence.insert(0, evidence_note)
            return {
                "text": f"{answer_prefix}{_build_fallback_text(query, rows)}".strip(),
                "evidence": evidence,
                "actions": ["질문 조건을 더 구체화", "같은 조건으로 재조회"],
            }
