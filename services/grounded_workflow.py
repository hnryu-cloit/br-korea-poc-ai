from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from common.gemini import Gemini
from common.query_logger import query_logger
from services.golden_query_resolver import get_default_resolver
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

_DEFAULT_FLOATING_CHAT_SYSTEM_INSTRUCTION = """
당신은 매장 운영 AI 비서다.
반드시 아래 원칙을 지킨다.
1) 단순 요약 금지. 질문 맥락에 맞는 실행 가능한 인사이트를 제공한다.
2) 모든 응답은 점주가 즉시 수행할 Action을 포함한다.
3) 수치 제안은 반드시 과거 데이터 또는 예측 모델 근거를 함께 제시한다.
4) 매장 맞춤형 답변을 제공한다.
5) 재고/생산 관련 질문은 1시간 이후 예측 오차 허용범위(±10%)와 찬스 로스 방지 알림 근거를 함께 제시한다.
6) 출력은 설명(text) + 출처/근거(evidence) + 추가 예상질문 3개(follow_up_questions) 형태를 유지한다.
""".strip()

_MAX_PROMPT_ROWS = 25
_MAX_PROMPT_REFERENCE_CHARS = 30000

# Gemini 입력에서 자동으로 제거할 메타/감사 컬럼 (가독성·시간 절약)
_PRUNE_COLUMNS_EXACT: set[str] = {
    "source_file",
    "source_sheet",
    "loaded_at",
    "updated_at",
    "created_at",
    "ingestion_id",
    "ingestion_run_id",
    "row_index",
    "row_values_json",
    "erp_send_dt",
}
_PRUNE_COLUMN_SUFFIXES: tuple[str, ...] = ("_at", "_by", "_ts")


def _is_meta_column(column: str) -> bool:
    name = (column or "").lower()
    if name in _PRUNE_COLUMNS_EXACT:
        return True
    return any(name.endswith(suffix) for suffix in _PRUNE_COLUMN_SUFFIXES)


def _drop_redundant_columns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """모든 행에서 None/빈 값만 갖는 컬럼은 제거해 입력 토큰을 줄인다."""
    if not rows:
        return rows
    columns = list(rows[0].keys())
    keep: list[str] = []
    for column in columns:
        if _is_meta_column(column):
            continue
        has_value = False
        for row in rows:
            value = row.get(column)
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            has_value = True
            break
        if has_value:
            keep.append(column)
    if len(keep) == len(columns):
        return rows
    return [{column: row.get(column) for column in keep} for row in rows]


# JSON 직렬화 시 Decimal 값을 float으로 변환
def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Unsupported type: {type(value)}")


def _limit_rows_for_prompt(
    rows: list[dict[str, Any]],
    *,
    max_rows: int = _MAX_PROMPT_ROWS,
    max_reference_chars: int = _MAX_PROMPT_REFERENCE_CHARS,
) -> tuple[list[dict[str, Any]], bool]:
    if not rows:
        return [], False

    capped_rows = rows[:max_rows]
    limited_rows: list[dict[str, Any]] = []
    current_chars = 2
    for row in capped_rows:
        row_json = json.dumps(row, ensure_ascii=False, default=_json_default)
        extra_chars = len(row_json) + (1 if limited_rows else 0)
        if limited_rows and current_chars + extra_chars > max_reference_chars:
            break
        limited_rows.append(row)
        current_chars += extra_chars

    if not limited_rows:
        limited_rows = capped_rows[:1]

    was_truncated = len(limited_rows) < len(rows)
    return limited_rows, was_truncated


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


_COLUMN_LABELS: dict[str, str] = {
    "item_cd": "품목코드",
    "item_nm": "품목명",
    "store_cd": "매장코드",
    "masked_stor_cd": "매장코드",
    "stor_cd": "매장코드",
    "store_nm": "매장명",
    "sale_dt": "일자",
    "sale_amt": "매출액",
    "sale_qty": "판매량",
    "ord_cnt": "주문건수",
    "ord_qty": "발주수량",
    "auto_ord_qty": "자동발주",
    "manual_ord_qty": "수동발주",
    "confrm_qty": "확정수량",
    "confirm_qty": "확정수량",
    "stk_avg": "평균재고",
    "stk_rt": "재고율(%)",
    "disuse_qty": "폐기량",
    "tmzon_div": "시간대",
    "channel": "채널",
    "ho_chnl_div": "채널",
    "pay_dc_nm": "결제수단",
    "pay_way_cd": "결제수단",
}


def _humanize_column(column: str) -> str:
    if not column:
        return ""
    return _COLUMN_LABELS.get(column.lower(), "")


def _format_cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, (int, float)):
        numeric = float(value)
        if abs(numeric) < 1e-12:
            return "0"
        if any(token in column.lower() for token in ("tmzon", "hour")) and abs(numeric - round(numeric)) < 1e-9:
            return f"{int(round(numeric))}시"
        return _format_number(numeric)

    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{1,2}", text) and any(token in column.lower() for token in ("tmzon", "hour")):
        return f"{int(text)}시"
    if re.fullmatch(r"-?\d+(?:\.\d+)?[eE][+-]?\d+", text):
        try:
            numeric = float(text)
        except ValueError:
            return text
        if abs(numeric) < 1e-12:
            return "0"
        return _format_number(numeric)
    return text


# LLM 응답이 비어 있거나 검증 실패 시 보여줄 최소한의 폴백
# (정형 분석은 LLM 책임이고, 폴백은 데이터를 가독성 있게만 보여 주는 안내 수준에 둔다)
def _build_fallback_text(query: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "조회 결과가 없습니다."

    name_keys = ("item_nm", "store_nm", "channel", "ho_chnl_div", "pay_dc_nm")
    top_rows = rows[:3]

    metric_columns: list[str] = []
    if top_rows:
        for column in top_rows[0].keys():
            if column in name_keys:
                continue
            if not _humanize_column(column):
                continue
            if _format_cell(column, top_rows[0].get(column)) == "":
                continue
            metric_columns.append(column)
            if len(metric_columns) >= 3:
                break

    def _row_title(row: dict[str, Any]) -> str:
        for key in name_keys:
            value = row.get(key)
            if value is None:
                continue
            text_value = _format_cell(key, value)
            if text_value:
                return text_value
        return "-"

    intro = (
        f"AI 분석 답변을 생성하지 못해 조회 결과를 정리해 드립니다. "
        f"(총 {len(rows)}건, 상위 {len(top_rows)}건 표시)"
    )

    if metric_columns:
        header_cells = ["품목/항목"] + [_humanize_column(c) for c in metric_columns]
        sep = ["---"] * len(header_cells)
        body_lines = [
            "| " + " | ".join(header_cells) + " |",
            "| " + " | ".join(sep) + " |",
        ]
        for row in top_rows:
            cells = [_row_title(row)]
            for column in metric_columns:
                cells.append(_format_cell(column, row.get(column)) or "-")
            body_lines.append("| " + " | ".join(cells) + " |")
        body = "\n".join(body_lines)
    else:
        body = "\n".join(f"- {_row_title(row)}" for row in top_rows)

    return f"{intro}\n\n{body}"


def _is_numeric_consistent(query: str, answer_text: str, rows: list[dict[str, Any]]) -> bool:
    if not answer_text.strip():
        return False
    allowed = _extract_numbers(query)
    allowed.update(_numbers_from_rows(rows))
    for number in _extract_numbers(answer_text):
        # 식별자(품목코드/매장코드 등 6자 이상 정수)나 연도/회계연도 같은 4자리 정수, 등수(1~10)는 검증에서 제외
        if number >= 100000 and abs(number - round(number)) < 1e-9:
            continue
        if 1900 <= number <= 2100 and abs(number - round(number)) < 1e-9:
            continue
        if 1 <= number <= 10 and abs(number - round(number)) < 1e-9:
            continue
        if not _contains_number(allowed, number, tolerance=max(0.5, abs(number) * 0.02)):
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
        self.golden_resolver = get_default_resolver(gemini)

    def run(
        self,
        *,
        query: str,
        store_id: str,
        domain: str,
        reference_date: str | None = None,
        system_instruction: str | None = None,
        golden_query_only: bool = False,
    ) -> dict[str, Any]:
        # Main grounded path:
        # classify -> choose tables -> generate SQL -> execute -> compose answer.
        details = self.classifier.classify_details(query)
        masked_query = str(details["masked_query"])
        if details["blocked"]:
            follow_ups = self.golden_resolver.suggest_follow_up_queries(
                query=masked_query,
                domain=domain,
                limit=3,
            )
            return {
                "text": "민감 정보가 포함된 질문으로 분류되어 직접 답변할 수 없습니다.",
                "keywords": [],
                "intent": "sensitive",
                "evidence": [f"masked_fields={details['masked_fields']}"],
                "actions": ["민감 정보를 제거하고 다시 질문", "운영 지침 기준으로 재문의"],
                "follow_up_questions": follow_ups,
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
            follow_ups = self.golden_resolver.suggest_follow_up_queries(
                query=masked_query,
                domain=domain,
                limit=3,
            )
            return {
                "text": ordering_policy.unavailable_text,
                "keywords": self.extract_keywords(masked_query),
                "intent": "ordering policy clarification",
                "evidence": ["발주일 기준 컬럼이 현재 raw_order_extract에 없습니다."],
                "actions": list(ordering_policy.unavailable_actions),
                "follow_up_questions": follow_ups,
                "query_type": domain.upper(),
                "processing_route": "ordering_policy_guard",
                "relevant_tables": ["raw_order_extract"],
                "sql": None,
                "row_count": 0,
                "data_lineage": [],
                "masked_fields": details["masked_fields"],
            }

        golden_result = self.golden_resolver.resolve_and_execute(
            query=masked_query,
            domain=domain,
            store_id=store_id,
            reference_date=reference_date,
            executor=self.executor,
        )
        if golden_result:
            if ordering_policy.answer_prefix:
                golden_result["text"] = f"{ordering_policy.answer_prefix}{golden_result.get('text', '')}".strip()
            if ordering_policy.evidence_note:
                evidence = golden_result.get("evidence", [])
                if ordering_policy.evidence_note not in evidence:
                    golden_result["evidence"] = [ordering_policy.evidence_note, *evidence]
            golden_result["follow_up_questions"] = self.golden_resolver.suggest_follow_up_queries(
                query=masked_query,
                domain=domain,
                exclude_query_id=golden_result.get("matched_query_id"),
                limit=3,
            )

            golden_result.update(
                {
                    "keywords": self.extract_keywords(masked_query),
                    "query_type": domain.upper(),
                    "processing_route": "golden_query_hit",
                    "data_lineage": query_logger.get_history(f"{domain}_golden_query"),
                    "masked_fields": details["masked_fields"],
                }
            )
            query_logger.clear_history()
            return golden_result

        if golden_query_only:
            ranked = self.golden_resolver.rank_candidates(masked_query, domain, limit=3)
            overlap = [
                {
                    "query_id": item.candidate.query_id,
                    "intent_id": item.candidate.intent_id,
                    "question": item.candidate.question,
                    "score": round(item.final_score, 4),
                }
                for item in ranked
            ]
            return {
                "text": "죄송합니다. 현재 문의주신 답변에 대해서는 준비된 골든쿼리가 없습니다. 유사 질문을 참고해 다시 문의해 주세요.",
                "keywords": self.extract_keywords(masked_query),
                "intent": "golden_query_miss",
                "evidence": [
                    "현재 질문은 골든쿼리 유사도 임계치 미달입니다.",
                    f"도메인: {domain}",
                ],
                "actions": ["유사 질문 중 하나를 선택해 다시 질문", "기간/상품/지표 조건을 더 구체화"],
                "follow_up_questions": [item["question"] for item in overlap][:3],
                "query_type": domain.upper(),
                "processing_route": "golden_query_miss_block",
                "relevant_tables": [],
                "sql": None,
                "row_count": 0,
                "data_lineage": [],
                "masked_fields": details["masked_fields"],
                "overlap_candidates": overlap,
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
            system_instruction=system_instruction,
        )
        answer["follow_up_questions"] = self.golden_resolver.suggest_follow_up_queries(
            query=masked_query,
            domain=domain,
            limit=3,
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
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        # Converts grounded rows into answer, evidence, and actions.
        active_system_instruction = system_instruction or _DEFAULT_FLOATING_CHAT_SYSTEM_INSTRUCTION
        # 메타/감사·전부-Null 컬럼 제거 후 행/문자수 캡 적용
        pruned_rows = _drop_redundant_columns(rows)
        prompt_rows, was_truncated = _limit_rows_for_prompt(pruned_rows)
        reference_columns = list(prompt_rows[0].keys()) if prompt_rows else []
        request_payload = {
            "system_prompt": active_system_instruction,
            "user_query": query,
            "sql_query": sql,
            "reference_data": {
                "columns": reference_columns,
                "rows": prompt_rows,
                "row_count": len(rows),
                "included_row_count": len(prompt_rows),
                "truncated": was_truncated,
                "omitted_row_count": max(len(rows) - len(prompt_rows), 0),
            },
            "metadata": {
                "domain": domain,
                "keywords": keywords,
                "intent": intent,
                "relevant_tables": relevant_tables,
                "queried_period": queried_period or {},
            },
        }
        request_payload_json = json.dumps(request_payload, ensure_ascii=False, default=_json_default)
        prompt = f"""
당신은 매장 운영 데이터를 설명하는 분석가입니다.
아래 JSON 입력의 reference_data.rows 만을 근거로, 점주가 한눈에 이해할 수 있는 분석형 답변을 작성하세요.

[입력 JSON]
{request_payload_json}

[text 작성 가이드 — 형식은 자율, 다만 다음 원칙을 따르세요]
- **핵심 요약은 필수**입니다. 첫 줄 또는 첫 단락에서 사용자 질문에 대한 결론을 한두 문장으로 제시하세요.
  (예: "이번 주 수동 발주는 총 OO건으로 지난주 대비 약 O% 증가했고, 베이커리류에 집중되었습니다.")
- 그 다음에 데이터의 성격에 맞는 형식을 자유롭게 선택하세요. 예를 들어:
  · 비교가 필요한 데이터는 마크다운 표(| 품목 | 값 | ... |)로
  · 단일 지표/추세는 짧은 bullet 또는 문장으로
  · 카테고리·이상치가 있으면 "주목해야 할 포인트" 또는 "분석 및 시사점" 섹션으로
- 동일한 템플릿을 매번 반복하지 말고, 데이터의 형태(품목 비교/시간대 분포/단일 지표 등)에 맞게 다르게 정리하세요.
- 줄바꿈은 \\n 으로, 강조는 **bold**, 표는 마크다운 표를 사용하세요. 모든 마크다운은 text 문자열 안에 그대로 포함하세요.

[엄수 규칙]
- reference_data.rows 에 없는 수치·품목명·날짜는 절대 만들어내지 마세요. 추정·일반화는 가능하지만 새 숫자는 금지.
- rows 가 비어 있으면 text 에 "조회 결과가 없습니다." 만 작성하세요.
- 0 이거나 0E-20 처럼 표기된 값은 모두 0 으로 다루세요.
- text 는 한국어 기준 800자 이내로 간결하게 작성하세요.
- evidence: 출처(조회 테이블·기간·총 건수) 1~3개.
- actions: 점주가 즉시 실행할 후속 액션 2~3개(각 80자 이내).
- follow_up_questions: 같은 도메인의 추가 질문 3개(중복 금지, 각 30자 이내).

반드시 JSON 으로만 답하세요.
{{
  "text": "마크다운 답변 본문 (핵심 요약 필수, 이후 형식 자율)",
  "evidence": ["근거 1", "근거 2"],
  "actions": ["후속 액션 1", "후속 액션 2"],
  "follow_up_questions": ["추가 질문 1", "추가 질문 2", "추가 질문 3"]
}}
"""
        try:
            raw = self.gemini.call_gemini_text(
                prompt,
                system_instruction=active_system_instruction,
                response_type="application/json",
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            follow_ups = data.get("follow_up_questions", [])
            if not isinstance(follow_ups, list):
                follow_ups = []
            follow_ups = [str(item).strip() for item in follow_ups if str(item).strip()][:3]
            answer = {
                "text": f"{answer_prefix}{data.get('text', '')}".strip(),
                "evidence": data.get("evidence", []),
                "actions": data.get("actions", []),
                "follow_up_questions": follow_ups,
            }
            if evidence_note and evidence_note not in answer["evidence"]:
                answer["evidence"] = [evidence_note, *answer["evidence"]]
            if answer["text"] and not _is_numeric_consistent(query, str(answer["text"]), rows):
                # 텍스트 자체는 살리되 검증 메모를 evidence 앞에 표시해 사용자가 인지하도록 한다
                logger.warning("numeric consistency check failed (text retained) query=%s", query)
                note = "AI 답변의 일부 수치가 조회 결과와 일치하지 않을 수 있어 함께 확인이 필요합니다."
                if note not in answer["evidence"]:
                    answer["evidence"] = [note, *answer["evidence"]]
            elif not str(answer["text"]).strip():
                # 빈 답변일 때만 결정론적 폴백 사용
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
                "follow_up_questions": [],
            }
