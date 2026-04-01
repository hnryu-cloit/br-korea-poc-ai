from __future__ import annotations
from typing import Any, Dict

class SemanticLayer:
    """
    지능형 시맨틱 레이어:
    자연어의 추상적인 표현을 실제 비즈니스 수식(KPI)으로 매핑합니다.
    이를 통해 AI가 임의로 수치를 계산하는 것을 방지하고 일관된 지표를 제공합니다.
    """
    def __init__(self):
        # 비즈니스 및 통계 지표 정의 (Data Governance)
        self.metrics_definition = {
            "장사가 잘됐다": "매출액이 7일 이동 평균(SMA) 대비 5% 이상 상승했을 때",
            "재고 위험": "1시간 후 예상 재고가 최근 4주 평균 생산량의 10% 미만일 때",
            "판매 급증": "Z-Score 분석 결과 매출액이 표준 편차의 2배 이상 상회하는 이상치 발생 시",
            "배달 성과 저조": "전체 매출 중 배달 비중이 20% 미만이거나 취소율이 5% 이상일 때"
        }
        # BI 데이터 마트 테이블 구조 정의 (Metadata)
        self.data_marts = {
            "daily_sales_summary": ["store_id", "date", "total_sales", "moving_avg_7d", "outlier_flag"],
            "inventory_health": ["sku_id", "current_stock", "predicted_stock_1h", "burn_rate_avg"]
        }

    def get_logic(self, query: str) -> str:
        """질문에 포함된 비즈니스 용어의 정의를 반환합니다."""
        for term, logic in self.metrics_definition.items():
            if term in query:
                return f"[비즈니스 로직 적용]: {term} = {logic}"
        return "[표준 분석 로직 적용]"

    def apply_guardrail(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """분석 결과에 비즈니스 정책을 강제 적용합니다."""
        # 예: 수익이 마이너스인 경우 특정 경고 문구 강제 포함 등
        if raw_data.get("profit_margin", 100) < 0:
            raw_data["actions"].insert(0, "즉시 원가 분석 및 프로모션 중단 검토가 필요합니다.")
        return raw_data
