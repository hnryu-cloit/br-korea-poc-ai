from __future__ import annotations

import json
import logging
from typing import Any

from common.gemini import Gemini
from schemas.contracts import SalesInsight, SalesQueryRequest, SalesQueryResponse
from services.query_routing import QueryClassifier, SemanticLayer
from services.sql_pipeline import QueryExecutionError, QueryExecutor, SQLGenerator

logger = logging.getLogger(__name__)

_MAX_DISPLAY_ROWS = 30  # LLM 프롬프트에 포함할 최대 데이터 행 수

_ANSWER_SYSTEM = """\
당신은 한국 베이커리 프랜차이즈 점주를 돕는 데이터 분석 AI입니다.
아래 [실제 조회 데이터]만을 근거로 답변을 생성하세요.
데이터에 없는 수치는 절대 추측하거나 지어내지 마세요.
"""


class GroundedSalesAnalyzer:
    """
    Text-to-SQL 기반 매출 분석기.

    파이프라인:
        1. QueryClassifier  — 민감 질의 차단
        2. SemanticLayer    — 질의 유형 분류 (→ 스키마 힌트 선택)
        3. SQLGenerator     — LLM이 SQL 추론
        4. QueryExecutor    — 실제 DB 조회
        5. 근거 기반 답변 생성 — 조회 데이터 + SQL을 evidence에 포함
    """

    def __init__(self, gemini: Gemini, db_url: str | None = None) -> None:
        self.gemini = gemini
        self.classifier = QueryClassifier()
        self.semantic_layer = SemanticLayer()
        self.sql_generator = SQLGenerator(gemini)
        self.executor = QueryExecutor(db_url)

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def analyze(self, payload: SalesQueryRequest) -> SalesQueryResponse:
        """Text-to-SQL 파이프라인 기반 질의 처리 및 근거 데이터 포함 응답 반환"""
        # 1. 민감 질의 차단
        if self.classifier.classify(payload.query) == "SENSITIVE":
            logger.warning("민감 질의 차단: %s", payload.query[:40])
            return self._sensitive_response()

        # 2. 질의 유형 분류 (스키마 힌트용)
        query_type = self._classify_query_type(payload.query)

        # 3. SQL 생성
        try:
            generated = self.sql_generator.generate(
                query=payload.query,
                store_id=payload.store_id,
                query_type=query_type,
            )
        except Exception as e:
            logger.error("SQL 생성 실패: %s", e)
            return self._error_response(str(e))

        # 4. 실제 DB 조회
        try:
            rows, columns = self.executor.run(generated.sql, payload.store_id)
        except QueryExecutionError as e:
            logger.error("쿼리 실행 실패: %s", e)
            return self._error_response(str(e))

        if not rows:
            return SalesQueryResponse(
                answer=SalesInsight(
                    text="조회된 데이터가 없습니다. 기간을 조정하거나 다른 질문을 시도해 보세요.",
                    evidence=[f"실행 SQL: {generated.sql}"],
                    actions=["조회 기간 확장", "다른 상품/조건으로 재질문"],
                ),
                source_data_period="해당 없음",
            )

        # 5. 근거 기반 답변 생성
        return self._generate_grounded_answer(payload.query, generated, rows, columns)

    # ------------------------------------------------------------------
    # private
    # ------------------------------------------------------------------

    def _classify_query_type(self, query: str) -> str:
        """SemanticLayer의 target_data_type을 sql_generator의 query_type 키로 매핑."""
        target_data_type, _ = self.semantic_layer.parse_query_intent(query)
        mapping = {
            "payment": "channel",
            "channel": "channel",
            "campaign": "campaign",
            "hourly": "sales",
            "general_sales": "sales",
        }
        return mapping.get(target_data_type, "general")

    def _generate_grounded_answer(
        self,
        query: str,
        generated: Any,
        rows: list[dict],
        columns: list[str],
    ) -> SalesQueryResponse:
        display_rows = rows[:_MAX_DISPLAY_ROWS]
        data_str = _format_rows(display_rows, columns)
        total_rows = len(rows)

        prompt = f"""[점주 질문]
{query}

[실행된 SQL]
{generated.sql}

[조회 결과 ({total_rows}행{f', 상위 {_MAX_DISPLAY_ROWS}행 표시' if total_rows > _MAX_DISPLAY_ROWS else ''})]
{data_str}

위 실제 데이터를 바탕으로 점주에게 도움이 되는 답변을 생성하세요.
반드시 아래 JSON 형식으로만 응답하세요.

{{
  "text": "핵심 분석 요약 (실제 수치 인용)",
  "evidence": [
    "근거 1: 데이터에서 직접 확인된 수치 또는 사실",
    "근거 2: ...",
    "조회 쿼리: {generated.description}"
  ],
  "actions": ["실행 가능한 액션 1", "실행 가능한 액션 2", "실행 가능한 액션 3"]
}}"""

        try:
            raw = self.gemini.call_gemini_text(
                prompt,
                system_instruction=_ANSWER_SYSTEM,
                response_type="application/json",
            )
            data = json.loads(raw)
        except Exception as e:
            logger.error("답변 생성 오류: %s", e)
            return self._error_response(str(e))

        insight = SalesInsight(
            text=data.get("text", ""),
            evidence=data.get("evidence", []),
            actions=data.get("actions", []),
        )

        period = _infer_period(rows, columns)

        return SalesQueryResponse(
            answer=insight,
            source_data_period=period,
        )

    @staticmethod
    def _sensitive_response() -> SalesQueryResponse:
        return SalesQueryResponse(
            answer=SalesInsight(
                text="보안 정책에 따라 민감 정보가 포함된 질문은 처리할 수 없습니다.",
                evidence=["민감 키워드 탐지"],
                actions=["표준 마진 시뮬레이션 요청", "보안 대시보드 확인"],
            ),
            source_data_period="N/A",
        )

    @staticmethod
    def _error_response(detail: str) -> SalesQueryResponse:
        return SalesQueryResponse(
            answer=SalesInsight(
                text="데이터 조회 중 오류가 발생했습니다.",
                evidence=[detail],
                actions=["잠시 후 다시 시도", "질문을 다르게 표현해 보세요"],
            ),
            source_data_period="N/A",
        )


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _format_rows(rows: list[dict], columns: list[str]) -> str:
    """테이블 형식 문자열로 변환 (LLM 가독성 최적화)."""
    if not rows:
        return "(결과 없음)"
    header = " | ".join(columns)
    separator = "-" * len(header)
    lines = [header, separator]
    for row in rows:
        lines.append(" | ".join(str(row.get(c, "")) for c in columns))
    return "\n".join(lines)


def _infer_period(rows: list[dict], columns: list[str]) -> str:
    """결과 행에서 날짜 범위를 추론해 사람이 읽기 좋은 기간 문자열 반환."""
    date_cols = [c for c in columns if "DT" in c.upper() or "DATE" in c.upper()]
    if not date_cols:
        return "조회 기간 미확인"
    col = date_cols[0]
    dates = sorted({str(r[col]) for r in rows if r.get(col)})
    if not dates:
        return "조회 기간 미확인"
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]} ~ {dates[-1]}"