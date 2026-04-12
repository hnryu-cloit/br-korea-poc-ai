from __future__ import annotations

import logging
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


class QueryClassifier:
    """
    규칙 기반 질의 유형 분류기.
    Gemini 호출 없이 키워드 매칭으로 동작한다.
    """

    def classify(self, query: str) -> QueryType:
        """질의를 분류해 QueryType 문자열 반환."""
        if any(kw in query for kw in _SENSITIVE_KEYWORDS):
            logger.info("QueryClassifier: SENSITIVE 질의 탐지 — '%s'", query[:40])
            return "SENSITIVE"

        if any(kw in query for kw in _CHANNEL_KEYWORDS):
            logger.info("QueryClassifier: CHANNEL 질의 — '%s'", query[:40])
            return "CHANNEL"

        if any(kw in query for kw in _COMPARISON_KEYWORDS):
            logger.info("QueryClassifier: COMPARISON 질의 — '%s'", query[:40])
            return "COMPARISON"

        if any(kw in query for kw in _PROFITABILITY_KEYWORDS):
            logger.info("QueryClassifier: PROFITABILITY 질의 — '%s'", query[:40])
            return "PROFITABILITY"

        if any(kw in query for kw in _NUMERIC_KEYWORDS):
            logger.info("QueryClassifier: NUMERIC 질의 — '%s'", query[:40])
            return "NUMERIC"

        logger.info("QueryClassifier: GENERAL 질의 — '%s'", query[:40])
        return "GENERAL"