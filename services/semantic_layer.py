from __future__ import annotations

from typing import Any, Dict, Tuple
import re

class SemanticLayer:
    """
    지능형 시맨틱 레이어 (Data RAG용):
    점주의 자연어 질의를 분석하여, 조회해야 할 데이터의 종류(Category)와 
    비즈니스 로직(Filter)을 매핑합니다.
    이를 통해 AI가 임의로 수치를 지어내는(환각) 것을 방지합니다.
    """
    def __init__(self):
        # 질의 키워드별 타겟 데이터소스 매핑
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
        고도화된 6가지 시나리오를 감지하여 구체적인 로직을 반환합니다.
        """
        target_data_type = "general_sales"
        applied_logic = "[표준 매출 분석 로직 적용]\n단순 요약을 피하고, 반드시 점주 매장에 맞춤화된 구체적이고 실행 가능한(Actionable) 인사이트를 도출하세요."

        query_lower = query.lower()

        # 1. 전년 동월 데이터 비교
        if re.search(r'(전년|작년).*(비교|어때|어땠)', query_lower):
            target_data_type = "general_sales"
            applied_logic = """[비즈니스 로직 적용]: 전년 동월 매출 비교
- 전년도 동일 월(예: 25년 2월)과 올해 대상 월(예: 26년 2월)의 매출 증감을 비교.
- 성장/감소율을 수치화하고, 원인 추정 및 후속 액션(마케팅, 재고 준비 등)을 제안하세요.
- 절대 단순 요약을 하지 마세요."""

        # 2. 유사상권 배달 건수 비교
        elif re.search(r'(유사\s*상권|동일\s*상권).*(배달|딜리버리).*(건수|매출)', query_lower):
            target_data_type = "channel"
            applied_logic = """[비즈니스 로직 적용]: 유사상권 배달 건수/매출 비교
- 내 점포의 전주/전월 배달 건수 및 매출을 유사 상권의 평균과 비교.
- 배달앱(배민, 쿠팡이츠 등)별 점유율을 확인하고, 내 점포가 유사상권 대비 부진한 채널을 파악해 타겟 프로모션이나 깃발 꽂기 등 구체적인 액션을 제안하세요."""

        # 3. T데이 행사 비교 (최근 vs 이전, 상권 평균 비교)
        elif re.search(r'(티데이|t\s*데이|tday)', query_lower):
            target_data_type = "campaign"
            applied_logic = """[비즈니스 로직 적용]: T-Day(티데이) 실적 분석
- 최근 T데이와 이전 T데이의 실적(매출액, 객수)을 비교.
- 동일 상권 평균 매출과 내 점포의 T데이 매출을 비교하여 상대적 퍼포먼스 평가.
- '매출/재고율'에 대한 간단한 리뷰를 포함하고, 다음 T데이를 위한 재고 발주량 및 인력 배치 가이드를 실행 가능한 인사이트로 제공하세요."""

        # 4. 특정 제품 비교
        elif re.search(r'(특정\s*제품|미니도넛|글레이즈드|\d{6}).*(비교|어때)', query_lower):
            target_data_type = "general_sales"
            applied_logic = """[비즈니스 로직 적용]: 특정 상품 전월/전년 대비 비교
- 질의에 언급된 특정 상품(예: 미니도넛, 글레이즈드 등)의 전월/전년 대비 매출 금액 및 판매량 비교.
- 해당 상품의 판매 추이를 분석하고, 연관 상품 진열(Cross-selling), 콤보 메뉴 구성 등의 Actionable Insight를 제공하세요."""

        # 5. 배달채널별 매출 및 동일상권 비교
        elif re.search(r'(배달\s*채널|쿠팡이츠|배민|해피오더).*(비교|알려줘)', query_lower):
            target_data_type = "channel"
            applied_logic = """[비즈니스 로직 적용]: 채널별(온라인) 배달 매출 및 상권 비교
- 배달 채널(쿠팡이츠, 요기요, 배민, 해피오더 등)별 매출 비중을 분석.
- 동일 상권 평균의 채널별 매출 비중과 내 점포를 비교.
- 수수료와 프로모션 효율을 고려하여 점주가 수익을 극대화할 수 있도록 채널 운영 최적화(특정 앱 할인 쿠폰 발행 등) 방안을 제안하세요."""

        # 6. 내 점포 vs 가맹점 평균 비교
        elif re.search(r'(가맹점|테스트|평균).*(비교)', query_lower) or re.search(r'평균\s*매출', query_lower):
            target_data_type = "general_sales"
            applied_logic = """[비즈니스 로직 적용]: 내 점포 vs 테스트 가맹점(10개) 평균 매출 비교
- 일별 또는 월별 내 점포 매출과 가맹점(테스트 10곳) 평균 매출을 대조.
- 내 점포가 가맹점 평균보다 저조한 요일이나 시간대를 식별하고, 해당 시간대에 적용할 타임 세일이나 인력 효율화 등 구체적 액션 아이템을 도출하세요."""

        else:
            # 타겟 데이터 분류 (일반적인 경우)
            for keyword, data_type in self.data_routing_rules.items():
                if keyword in query_lower:
                    target_data_type = data_type
                    break

        return target_data_type, applied_logic
