from __future__ import annotations

import logging
import re
from typing import Literal

logger = logging.getLogger(__name__)

QueryType = Literal["SENSITIVE", "NUMERIC", "CHANNEL", "COMPARISON", "PROFITABILITY", "GENERAL"]

_SENSITIVE_KEYWORDS = [
    "원가", "마진", "급여", "직원 월급", "순이익", "영업이익",
    "경쟁사", "타매장", "타 매장", "개인정보", "가맹비", "로열티",
    "본사 수수료", "계약서",
]

_NUMERIC_KEYWORDS = [
    "얼마", "건수", "몇 개", "몇개", "수량", "매출액", "총매출",
    "판매량", "판매 수", "몇 건", "몇건", "%", "비율", "비중",
]

_CHANNEL_KEYWORDS = [
    "배달", "채널", "해피오더", "배민", "쿠팡", "온라인", "오프라인",
    "결제수단", "카드", "현금", "간편결제", "페이",
]

_COMPARISON_KEYWORDS = [
    "비교", "성장", "전월", "전주", "지난달", "지난주", "지난 달",
    "지난 주", "증감", "대비", "같은 기간", "전년",
]

_PROFITABILITY_KEYWORDS = [
    "수익", "이익", "BEP", "손익", "시뮬레이션", "흑자", "적자",
]

_PII_PATTERNS: list[tuple[str, str, str]] = [
    (r"0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}", "***-****-****", "phone_number"),
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "***@***", "email"),
]


class QueryClassifier:
    """
    규칙 기반 질의 유형 분류기.
    Gemini 호출 없이 키워드 매칭으로 동작한다.
    """

    def mask_sensitive_fields(self, query: str) -> tuple[str, list[str]]:
        """질의 내 PII를 마스킹하고 필드 목록을 반환합니다."""
        masked_query = query
        masked_fields: list[str] = []
        for pattern, replacement, field_name in _PII_PATTERNS:
            updated_query, count = re.subn(pattern, replacement, masked_query)
            if count > 0 and field_name not in masked_fields:
                masked_fields.append(field_name)
            masked_query = updated_query
        return masked_query, masked_fields

    def classify_details(self, query: str) -> dict[str, object]:
        """질의 분류 결과와 마스킹 메타데이터를 함께 반환합니다."""
        masked_query, masked_fields = self.mask_sensitive_fields(query)

        if any(kw in masked_query for kw in _SENSITIVE_KEYWORDS):
            query_type: QueryType = "SENSITIVE"
        elif any(kw in masked_query for kw in _CHANNEL_KEYWORDS):
            query_type = "CHANNEL"
        elif any(kw in masked_query for kw in _COMPARISON_KEYWORDS):
            query_type = "COMPARISON"
        elif any(kw in masked_query for kw in _PROFITABILITY_KEYWORDS):
            query_type = "PROFITABILITY"
        elif any(kw in masked_query for kw in _NUMERIC_KEYWORDS):
            query_type = "NUMERIC"
        else:
            query_type = "GENERAL"

        logger.info(
            "QueryClassifier: type=%s masked_fields=%s query='%s'",
            query_type,
            masked_fields,
            masked_query[:40],
        )
        return {
            "query_type": query_type,
            "masked_query": masked_query,
            "masked_fields": masked_fields,
            "blocked": query_type == "SENSITIVE",
        }

    def classify(self, query: str) -> QueryType:
        """질의를 분류해 QueryType 문자열 반환."""
        return self.classify_details(query)["query_type"]  # type: ignore[return-value]
