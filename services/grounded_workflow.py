from __future__ import annotations

import json
import logging
import re
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
    "대한",
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


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported type: {type(value)}")


class GroundedWorkflow:
    """Shared grounded workflow for all LLM-backed business answers."""

    def __init__(self, gemini: Gemini, db_url: str | None = None) -> None:
        self.gemini = gemini
        self.classifier = QueryClassifier()
        self.semantic_layer = SemanticLayer()
        self.sql_generator = SQLGenerator(gemini)
        self.executor = QueryExecutor(db_url)

    def run(self, *, query: str, store_id: str, domain: str) -> dict[str, Any]:
        details = self.classifier.classify_details(query)
        masked_query = str(details["masked_query"])
        if details["blocked"]:
            return {
                "text": "민감 정보가 포함된 질문으로 분류되어 직접 답변할 수 없습니다.",
                "keywords": [],
                "intent": "sensitive",
                "evidence": [f"masked_fields={details['masked_fields']}"],
                "actions": ["민감 정보를 제외하고 다시 질문", "운영 지표 기준으로 재질문"],
                "query_type": "SENSITIVE",
                "processing_route": "policy_block",
                "relevant_tables": [],
                "sql": None,
                "row_count": 0,
                "data_lineage": [],
                "masked_fields": details["masked_fields"],
            }

        keywords = self.extract_keywords(masked_query)
        intent, relevant_tables = self.analyze_intent(masked_query, keywords, domain)
        generated = self.sql_generator.generate(
            masked_query,
            store_id,
            query_type=_DOMAIN_TO_QUERY_TYPE.get(domain, "general"),
            table_hints_override=relevant_tables,
            intent_summary=intent,
        )
        rows, _ = self.executor.run(
            generated.sql,
            store_id,
            agent_name=_DOMAIN_TO_AGENT.get(domain, domain),
            target_tables=generated.relevant_tables,
            params={"store_id": store_id, "keywords": keywords, "intent": intent},
        )
        answer = self.compose_answer(
            query=masked_query,
            domain=domain,
            keywords=keywords,
            intent=intent,
            relevant_tables=generated.relevant_tables,
            sql=generated.sql,
            queried_period=generated.queried_period,
            rows=rows,
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
        query_type = _DOMAIN_TO_QUERY_TYPE.get(domain, "general")
        schema_context = get_schema_context(get_table_hints(query_type))
        target_data_type, business_logic = self.semantic_layer.parse_query_intent(query)
        prompt = f"""
질문에서 포착한 키워드와 도메인 정보를 바탕으로 조회 의도와 필요한 테이블을 정리하세요.

[도메인]
{domain}

[질문]
{query}

[키워드]
{keywords}

[기본 의도 힌트]
- semantic_type: {target_data_type}
- business_logic: {business_logic}

[사용 가능 스키마]
{schema_context}

반드시 JSON으로만 답하세요.
{{
  "intent": "질문의 의도를 한 문장으로 요약",
  "relevant_tables": ["테이블1", "테이블2"]
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
    ) -> dict[str, Any]:
        serialized_rows = json.dumps(rows[:30], ensure_ascii=False, default=_json_default)
        prompt = f"""
당신은 매장 운영 데이터를 설명하는 분석가입니다.
아래 단계의 결과만 근거로 자연스러운 답변을 만드세요.

[질문]
{query}

[포착 키워드]
{keywords}

[분석된 의도]
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
- 조회 결과에 없는 수치나 사실을 추가하지 마세요.
- 값이 없으면 데이터가 없다고 명확히 말하세요.
- 답변 text에는 조회된 숫자나 항목을 자연스럽게 녹여 쓰세요.
- queried_period가 별도 필드로 제공되므로 답변 text에서는 기간을 반복하지 마세요.
- 날짜나 기간 자체가 답변의 핵심일 때만 text에 날짜를 언급하세요.

반드시 JSON으로만 답하세요.
{{
  "text": "사용자에게 보여줄 자연스러운 답변",
  "evidence": ["근거 1", "근거 2"],
  "actions": ["후속 액션 1", "후속 액션 2"]
}}
"""
        try:
            raw = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(raw) if isinstance(raw, str) else raw
            return {
                "text": data.get("text", ""),
                "evidence": data.get("evidence", []),
                "actions": data.get("actions", []),
            }
        except Exception as exc:
            logger.error("answer composition failed: %s", exc)
            return {
                "text": "데이터 조회는 완료되었으나 답변 생성 중 오류가 발생했습니다.",
                "evidence": [f"조회 테이블: {', '.join(relevant_tables)}", f"조회 건수: {len(rows)}"],
                "actions": ["질문을 조금 더 구체화", "동일 조건으로 재조회"],
            }
