from __future__ import annotations

import logging
import re
from typing import Literal, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# QueryClassifier
# ---------------------------------------------------------------------------

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
    """규칙 기반 질의 유형 분류기. Gemini 호출 없이 키워드 매칭으로 동작한다."""

    def classify(self, query: str) -> QueryType:
        if any(kw in query for kw in _SENSITIVE_KEYWORDS):
            logger.info("QueryClassifier: SENSITIVE — '%s'", query[:40])
            return "SENSITIVE"
        if any(kw in query for kw in _CHANNEL_KEYWORDS):
            logger.info("QueryClassifier: CHANNEL — '%s'", query[:40])
            return "CHANNEL"
        if any(kw in query for kw in _COMPARISON_KEYWORDS):
            logger.info("QueryClassifier: COMPARISON — '%s'", query[:40])
            return "COMPARISON"
        if any(kw in query for kw in _PROFITABILITY_KEYWORDS):
            logger.info("QueryClassifier: PROFITABILITY — '%s'", query[:40])
            return "PROFITABILITY"
        if any(kw in query for kw in _NUMERIC_KEYWORDS):
            logger.info("QueryClassifier: NUMERIC — '%s'", query[:40])
            return "NUMERIC"
        logger.info("QueryClassifier: GENERAL — '%s'", query[:40])
        return "GENERAL"


# ---------------------------------------------------------------------------
# SemanticLayer
# ---------------------------------------------------------------------------

_DATA_ROUTING_RULES: dict[str, str] = {
    "결제": "payment", "페이": "payment", "카드": "payment",
    "시간대": "hourly", "아침": "hourly", "오후": "hourly",
    "캠페인": "campaign", "행사": "campaign", "프로모션": "campaign",
    "온라인": "channel", "오프라인": "channel", "배달": "channel",
    "티데이": "campaign", "t데이": "campaign",
}


class SemanticLayer:
    """
    자연어 질의를 분석해 조회 대상 데이터 유형과 비즈니스 로직을 반환한다.
    LLM 없이 규칙 기반으로 동작해 빠르고 결정적이다.
    """

    def parse_query_intent(self, query: str) -> Tuple[str, str]:
        target = "general_sales"
        logic = "[표준 매출 분석 로직] 점주 맞춤형 실행 가능한(Actionable) 인사이트를 도출하세요."
        q = query.lower()

        if re.search(r'(전년|작년).*(비교|어때|어땠)', q):
            target = "general_sales"
            logic = "[비즈니스 로직] 전년 동월 매출 비교 — 성장/감소율을 수치화하고 원인 추정 및 후속 액션을 제안하세요."

        elif re.search(r'(유사\s*상권|동일\s*상권).*(배달|딜리버리).*(건수|매출)', q):
            target = "channel"
            logic = "[비즈니스 로직] 유사상권 배달 비교 — 배달앱별 점유율을 확인하고 부진 채널 개선 액션을 제안하세요."

        elif re.search(r'(티데이|t\s*데이|tday)', q):
            target = "campaign"
            logic = "[비즈니스 로직] T-Day 실적 분석 — 이전 T데이 대비 실적과 동일 상권 평균을 비교하세요."

        elif re.search(r'(특정\s*제품|미니도넛|글레이즈드|\d{6}).*(비교|어때)', q):
            target = "general_sales"
            logic = "[비즈니스 로직] 특정 상품 전월/전년 비교 — Cross-selling 및 콤보 구성 인사이트를 제공하세요."

        elif re.search(r'(배달\s*채널|쿠팡이츠|배민|해피오더).*(비교|알려줘)', q):
            target = "channel"
            logic = "[비즈니스 로직] 채널별 배달 매출 비교 — 수수료 효율을 고려한 채널 운영 최적화 방안을 제안하세요."

        elif re.search(r'(가맹점|테스트|평균).*(비교)', q) or re.search(r'평균\s*매출', q):
            target = "general_sales"
            logic = "[비즈니스 로직] 가맹점 평균 비교 — 저조 시간대를 식별하고 타임 세일 등 액션을 도출하세요."

        else:
            for keyword, data_type in _DATA_ROUTING_RULES.items():
                if keyword in q:
                    target = data_type
                    break

        return target, logic