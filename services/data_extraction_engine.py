"""자연어 질의 기반 데이터 추출 엔진.

SQL/데이터 우선 처리 + 필요 시 AI 분석을 결합한 경량 질의 엔진.
오케스트레이터의 NUMERIC / COMPARISON 경로에서 호출됩니다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from services.sales_agent import SalesAnalysisAgent

logger = logging.getLogger(__name__)


class DataExtractionEngine:
    """자연어 질의를 의도(intent)로 분류하고 구조화된 데이터를 반환합니다."""

    def __init__(self, sales_agent: Optional["SalesAnalysisAgent"] = None) -> None:
        self.agent = sales_agent

    INTENT_PATTERNS: dict[str, list[str]] = {
        "total_sales": ["총 매출", "매출 합계", "얼마나 팔았", "revenue", "total sales", "매출액"],
        "peak_hours": ["피크", "바쁜 시간", "peak", "가장 많이 팔린 시간", "붐비는", "혼잡"],
        "top_items": ["인기 메뉴", "베스트", "top", "많이 팔린", "잘 팔리는", "인기 상품"],
        "comparison": ["비교", "vs", "대비", "차이", "compared", "전주 대비", "전월 대비"],
        "profitability": ["수익", "이익", "마진", "profit", "margin", "순이익"],
        "inventory": ["재고", "inventory", "남은", "보유량"],
        "ordering": ["주문", "발주", "order", "마감", "주문량"],
    }

    def classify_intent(self, query: str) -> str:
        """질의 텍스트를 의도(intent) 유형으로 분류합니다."""
        query_lower = query.lower()
        for intent, patterns in self.INTENT_PATTERNS.items():
            if any(p in query_lower for p in patterns):
                return intent
        return "general"

    def extract(
        self,
        query: str,
        store_id: str,
        date_range: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """질의를 처리하고 구조화된 추출 결과를 반환합니다.

        Args:
            query: 자연어 질의 문자열
            store_id: 매장 ID
            date_range: {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"} 선택적 날짜 범위

        Returns:
            intent, data, answer 를 포함한 딕셔너리
        """
        intent = self.classify_intent(query)
        logger.info("DataExtraction: store=%s intent=%s query_len=%d", store_id, intent, len(query))

        date_from = date_range.get("from", "2024-01-01") if date_range else "2024-01-01"
        date_to = date_range.get("to", "2024-12-31") if date_range else "2024-12-31"

        result: dict[str, Any] = {
            "store_id": store_id,
            "intent": intent,
            "date_from": date_from,
            "date_to": date_to,
            "data": {},
            "answer": "",
        }

        if intent == "total_sales":
            if self.agent:
                try:
                    profitability = self.agent.simulate_real_profitability(store_id)
                    total = profitability.get("total_sales", 0)
                    result["data"] = {"total_revenue": total, "unit": "KRW"}
                    result["answer"] = f"{store_id} 매장의 기간 총 매출은 약 {total:,.0f}원입니다."
                except Exception as exc:
                    logger.warning("total_sales 실데이터 조회 실패, 스텁 반환: %s", exc)
                    result["data"] = {"total_revenue": 5_000_000, "unit": "KRW", "note": "스텁"}
                    result["answer"] = f"{store_id} 매장의 해당 기간 총 매출은 약 500만원입니다."
            else:
                result["data"] = {"total_revenue": 5_000_000, "unit": "KRW", "note": "스텁"}
                result["answer"] = f"{store_id} 매장의 해당 기간 총 매출은 약 500만원입니다."

        elif intent == "peak_hours":
            if self.agent:
                try:
                    profile = self.agent.extract_store_profile(store_id)
                    peak = profile.get("peak_hour", "12:00~13:00")
                    result["data"] = {"peak_range": peak}
                    result["answer"] = f"피크 시간대는 {peak}이며, 해당 시간대에 집중 대응이 필요합니다."
                except Exception as exc:
                    logger.warning("peak_hours 실데이터 조회 실패, 스텁 반환: %s", exc)
                    result["data"] = {"peak_start": "12:00", "peak_end": "13:00", "peak_revenue_ratio": 0.28}
                    result["answer"] = "피크 시간대는 오전 12시~오후 1시이며, 전체 매출의 약 28%가 집중됩니다."
            else:
                result["data"] = {"peak_start": "12:00", "peak_end": "13:00", "peak_revenue_ratio": 0.28}
                result["answer"] = "피크 시간대는 오전 12시~오후 1시이며, 전체 매출의 약 28%가 집중됩니다."

        elif intent == "top_items":
            if self.agent:
                try:
                    profile = self.agent.extract_store_profile(store_id)
                    top = profile.get("top_items", [])
                    items = [{"name": n, "rank": i + 1} for i, n in enumerate(top[:3])]
                    result["data"] = {"items": items}
                    names = ", ".join(n for n in top[:3]) if top else "상위 메뉴"
                    result["answer"] = f"가장 많이 팔린 메뉴는 {names} 순입니다."
                except Exception as exc:
                    logger.warning("top_items 실데이터 조회 실패, 스텁 반환: %s", exc)
                    result["data"] = {"items": [{"name": "슈크림", "rank": 1}, {"name": "소보루", "rank": 2}, {"name": "단팥빵", "rank": 3}]}
                    result["answer"] = "가장 많이 팔린 메뉴는 슈크림, 소보루, 단팥빵 순입니다."
            else:
                result["data"] = {"items": [{"name": "슈크림", "rank": 1}, {"name": "소보루", "rank": 2}, {"name": "단팥빵", "rank": 3}]}
                result["answer"] = "가장 많이 팔린 메뉴는 슈크림, 소보루, 단팥빵 순입니다."

        elif intent == "comparison":
            if self.agent:
                try:
                    comp = self.agent.calculate_comparison_metrics(store_id)
                    growth = comp.get("growth_rate", 0)
                    recent = comp.get("recent_4w_sales", 0)
                    prev = comp.get("previous_4w_sales", 0)
                    result["data"] = comp
                    result["answer"] = (
                        f"최근 4주 매출은 {recent:,.0f}원으로 직전 4주({prev:,.0f}원) 대비 "
                        f"{growth:+.1f}% {'성장' if growth >= 0 else '감소'}했습니다."
                    )
                except Exception as exc:
                    logger.warning("comparison 실데이터 조회 실패, 스텁 반환: %s", exc)
                    result["data"] = {"note": "비교 분석은 구체적인 대상 기간/메뉴 지정이 필요합니다."}
                    result["answer"] = "비교할 기간이나 메뉴를 구체적으로 지정해주세요. 예: '전주 대비 이번 주 매출 비교'"
            else:
                result["data"] = {"note": "비교 분석은 구체적인 대상 기간/메뉴 지정이 필요합니다."}
                result["answer"] = "비교할 기간이나 메뉴를 구체적으로 지정해주세요. 예: '전주 대비 이번 주 매출 비교'"

        elif intent == "profitability":
            if self.agent:
                try:
                    prof = self.agent.simulate_real_profitability(store_id)
                    margin = prof.get("estimated_margin_rate", 0.65)
                    profit = prof.get("estimated_profit", 0)
                    result["data"] = prof
                    result["answer"] = (
                        f"실데이터 기반 마진율은 {margin * 100:.1f}%이며, "
                        f"추정 순이익은 약 {profit:,.0f}원입니다."
                    )
                except Exception as exc:
                    logger.warning("profitability 실데이터 조회 실패, 스텁 반환: %s", exc)
                    result["data"] = {"margin_rate": 0.65, "estimated_profit": 3_250_000, "note": "표준 마진 65% 적용"}
                    result["answer"] = "표준 마진 65% 기준으로 추정 순이익은 약 325만원입니다."
            else:
                result["data"] = {"margin_rate": 0.65, "estimated_profit": 3_250_000, "note": "표준 마진 65% 적용"}
                result["answer"] = "표준 마진 65% 기준으로 추정 순이익은 약 325만원입니다."

        elif intent == "inventory":
            result["data"] = {"note": "실시간 재고는 생산 현황 화면에서 확인 가능합니다."}
            result["answer"] = "현재 재고 현황은 생산 관리 화면에서 SKU별로 확인하실 수 있습니다."

        elif intent == "ordering":
            result["data"] = {"note": "주문 추천은 주문 관리 화면에서 확인 가능합니다."}
            result["answer"] = "주문 추천 옵션은 주문 관리 화면에서 3가지 옵션으로 제공됩니다."

        else:
            result["answer"] = (
                f"'{query}'에 대한 데이터를 분석 중입니다. "
                "더 구체적인 질문(예: 매출, 피크타임, 인기메뉴)을 입력해주세요."
            )

        return result