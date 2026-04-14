from __future__ import annotations

from typing import Tuple
import re


class SemanticLayer:
    """
    지능형 시맨틱 레이어 (Data RAG용):
    점주의 자연어 질의를 분석하여, 조회해야 할 데이터의 종류(Category)와
    비즈니스 로직(Filter)을 매핑합니다.
    이를 통해 AI가 임의로 수치를 지어내는(환각) 것을 방지합니다.
    """
    def __init__(self):
        self.data_routing_rules = {
            "결제": "payment",
            "페이": "payment",
            "카드": "payment",
            "시간대": "hourly",
            "아침": "hourly",
            "오후": "hourly",
            "캠페인": "campaign",
            "행사": "campaign",
            "프로모션": "campaign",
            "온라인": "channel",
            "오프라인": "channel",
            "배달": "channel",
            "티데이": "campaign",
            "t데이": "campaign"
        }

    def parse_query_intent(self, query: str) -> Tuple[str, str]:
        """
        질의를 분석하여 조회할 타겟 데이터와 비즈니스 로직을 반환합니다.
        """
        target_data_type = "general_sales"
        applied_logic = "[표준 매출 분석 로직 적용]\n단순 요약을 피하고, 반드시 점주 매장에 맞춤화된 구체적이고 실행 가능한(Actionable) 인사이트를 도출하세요."

        query_lower = query.lower()

        if re.search(r'(전년|작년).*(비교|어때|어땠)', query_lower):
            target_data_type = "general_sales"
            applied_logic = """[비즈니스 로직 적용]: 전년 동월 매출 비교
- 전년도 동일 월과 올해 대상 월의 매출 증감을 비교.
- 성장/감소율을 수치화하고, 원인 추정 및 후속 액션을 제안하세요."""

        elif re.search(r'(유사\s*상권|동일\s*상권).*(배달|딜리버리).*(건수|매출)', query_lower):
            target_data_type = "channel"
            applied_logic = """[비즈니스 로직 적용]: 유사상권 배달 건수/매출 비교
- 내 점포의 전주/전월 배달 건수 및 매출을 유사 상권의 평균과 비교.
- 배달앱별 점유율을 확인하고, 부진한 채널을 파악해 구체적인 액션을 제안하세요."""

        elif re.search(r'(티데이|t\s*데이|tday)', query_lower):
            target_data_type = "campaign"
            applied_logic = """[비즈니스 로직 적용]: T-Day(티데이) 실적 분석
- 최근 T데이와 이전 T데이의 실적을 비교.
- 동일 상권 평균 매출과 내 점포의 T데이 매출을 비교하여 상대적 퍼포먼스 평가."""

        elif re.search(r'(특정\s*제품|미니도넛|글레이즈드|\d{6}).*(비교|어때)', query_lower):
            target_data_type = "general_sales"
            applied_logic = """[비즈니스 로직 적용]: 특정 상품 전월/전년 대비 비교
- 특정 상품의 전월/전년 대비 매출 금액 및 판매량 비교.
- 연관 상품 진열(Cross-selling), 콤보 메뉴 구성 등의 Actionable Insight를 제공하세요."""

        elif re.search(r'(배달\s*채널|쿠팡이츠|배민|해피오더).*(비교|알려줘)', query_lower):
            target_data_type = "channel"
            applied_logic = """[비즈니스 로직 적용]: 채널별(온라인) 배달 매출 및 상권 비교
- 배달 채널별 매출 비중을 분석.
- 동일 상권 평균의 채널별 매출 비중과 내 점포를 비교."""

        elif re.search(r'(가맹점|테스트|평균).*(비교)', query_lower) or re.search(r'평균\s*매출', query_lower):
            target_data_type = "general_sales"
            applied_logic = """[비즈니스 로직 적용]: 내 점포 vs 가맹점 평균 매출 비교
- 일별 또는 월별 내 점포 매출과 가맹점 평균 매출을 대조.
- 저조한 요일이나 시간대를 식별하고 구체적 액션 아이템을 도출하세요."""

        else:
            for keyword, data_type in self.data_routing_rules.items():
                if keyword in query_lower:
                    target_data_type = data_type
                    break

        return target_data_type, applied_logic