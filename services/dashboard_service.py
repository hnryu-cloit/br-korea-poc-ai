from __future__ import annotations
import datetime
import pandas as pd
from typing import Dict, Any, List
from common.logger import init_logger
from schemas.dashboard import (
    DashboardOverviewResponse, DashboardAction, DashboardStat,
    DashboardCardsResponse, DashboardCard, PromptAction, CardHighlight, CardMetric,
    DashboardInsightsResponse, InsightItem, QuickLink,
    HomeDashboardResponse
)
from services.production_service import ProductionService
from services.ordering_service import OrderingService

try:
    from services.sales_service import SalesService
except ImportError:  # pragma: no cover - trimmed POC snapshot fallback
    class SalesService:  # type: ignore[too-many-ancestors]
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

logger = init_logger("dashboard_service")

class DashboardService:
    def __init__(self, prod_service: ProductionService, order_service: OrderingService, sales_service: SalesService):
        self.prod_service = prod_service
        self.order_service = order_service
        self.sales_service = sales_service

    def get_home_overview(self, mock_payload: dict, raw_data: dict) -> HomeDashboardResponse:
        """
        [HOME] 3대 에이전트의 핵심 지표를 취합하여 FE가 요청한 통합 대시보드 데이터를 생성합니다.
        """
        store_id = mock_payload["store_id"]
        target_date = mock_payload["target_date"]
        current_time = mock_payload["current_time"]

        # 1. 생산 현황 (가장 위험한 품목 추출)
        inv_df = pd.DataFrame(raw_data.get("inventory_data", []))
        prod_df = pd.DataFrame(raw_data.get("production_data", []))
        sales_df = pd.DataFrame(raw_data.get("sales_data", []))
        store_prod_df = pd.DataFrame(raw_data.get("store_production_data", []))

        dash_res = self.prod_service.get_dashboard_summary(
            store_id, target_date, inv_df, prod_df, sales_df, store_prod_df
        )
        
        critical_items = [s for s in dash_res.sku_list if s.status == "위험"]
        top_item = critical_items[0] if critical_items else dash_res.sku_list[0]
        
        # 2. 주문 관리 정보 (Mocked for POC)
        order_deadline_min = 17 
        
        # --- A. Dashboard Overview (상단) ---
        priority_actions = [
            DashboardAction(
                id="production-urgent-1",
                type="production",
                urgency="urgent" if top_item.status == "위험" else "important",
                badge_label=f"긴급 - {top_item.alert_message.split('.')[0]}",
                title=f"{top_item.item_nm} 생산 필요",
                description=f"현재 {top_item.current_qty}개 → 1시간 후 {top_item.predict_1h_qty}개 예상. 지금 생산 시 찬스로스 {top_item.chance_loss_reduction_pct}% 감소 가능",
                cta_label="생산관리 상세보기",
                cta_path="/production",
                focus_section="risk-skus",
                related_sku_id=top_item.item_cd,
                ai_reasoning=top_item.alert_message,
                impact_metric=f"찬스로스 {top_item.chance_loss_reduction_pct}% 감소",
                is_finished_good=not top_item.can_produce,
                confidence_score=0.92
            ),
            DashboardAction(
                id="ordering-deadline-1",
                type="ordering",
                urgency="important",
                badge_label="중요 - 주문 마감 임박",
                title=f"주문 마감 {order_deadline_min}분 남음",
                description="오늘 주문 미완료 - AI 추천 3안 검토 후 점주가 직접 확정 필요",
                cta_label="주문 검토하기",
                cta_path="/ordering",
                focus_section="deadline",
                ai_reasoning="과거 4주 판매 트렌드와 오늘 날씨(흐림, 18도)를 고려할 때 평소보다 10% 증량 발주를 권장합니다.",
                impact_metric="결품 방지율 98% 확보",
                confidence_score=0.88
            ),
            DashboardAction(
                id="sales-profit-1",
                type="sales",
                urgency="recommended",
                badge_label="권장 - 손익 확인",
                title="오늘 손익 확인 권장",
                description="어제 대비 매출 15% 증가 · 손익분기점 초과 달성",
                cta_label="손익분석 상세보기",
                cta_path="/sales",
                focus_section="profit-summary",
                ai_reasoning="배달 채널 프로모션 효과로 인해 객단가가 1,200원 상승하며 수익성이 크게 개선되었습니다.",
                impact_metric="영업이익 +342,000원",
                confidence_score=0.95
            )
        ]

        stats = [
            DashboardStat(key="production_risk_count", label="품절 위험 SKU", value=f"{dash_res.summary.critical_count}개", tone="danger"),
            DashboardStat(key="ordering_deadline_minutes", label="주문 마감까지", value=f"{order_deadline_min}분", tone="primary"),
            DashboardStat(key="today_profit_estimate", label="오늘 순이익 추정", value="+342,000원", tone="success"),
            DashboardStat(key="alert_count", label="알림 상태", value="긴급 2건", tone="default")
        ]

        overview_data = DashboardOverviewResponse(
            updated_at=current_time.strftime("%Y-%m-%d %H:%M"),
            target_date=target_date,
            store_id=store_id,
            priority_actions=priority_actions,
            stats=stats
        )

        # --- B. Dashboard Cards (중단 3대 에이전트 상세) ---
        cards_data = DashboardCardsResponse(
            cards=[
                DashboardCard(
                    domain="production",
                    title="생산 현황",
                    description="실시간 재고 및 1시간 후 예측",
                    cta_label="생산관리 상세보기",
                    cta_path="/production",
                    prompts=[
                        PromptAction(id="production-1", label="지금 생산해야 할 품목은?", prompt="지금 생산해야 할 품목은?"),
                        PromptAction(id="production-2", label="찬스 로스가 뭔가요?", prompt="찬스 로스가 뭔가요?"),
                        PromptAction(id="production-3", label="품절 처리 방법은?", prompt="품절 처리 방법은?")
                    ],
                    highlights=[
                        CardHighlight(title=f"{top_item.item_nm} 재고 소진 1시간 전", description=f"현재 재고 {top_item.current_qty}개 · 지금 생산 시 찬스 로스 {top_item.chance_loss_reduction_pct}% 감소 가능", tone="danger"),
                        CardHighlight(title="말차 도넛 소진 속도 빠름", description="평소 대비 30% 빠른 판매 속도 감지", tone="warning")
                    ],
                    metrics=[
                        CardMetric(label="품절 위험", value=f"{dash_res.summary.critical_count}개", tone="danger"),
                        CardMetric(label="찬스 로스 절감", value=f"{dash_res.summary.avg_chance_loss_reduction}%", tone="primary")
                    ]
                ),
                DashboardCard(
                    domain="ordering",
                    title="주문 관리",
                    description="주문 누락 방지 및 추천 검토",
                    cta_label="주문 검토하기",
                    cta_path="/ordering",
                    prompts=[
                        PromptAction(id="ordering-1", label="추천 주문량은?", prompt="추천 주문량은?"),
                        PromptAction(id="ordering-2", label="어제와 비교하면?", prompt="어제와 비교하면?"),
                        PromptAction(id="ordering-3", label="날씨 영향은?", prompt="날씨 영향은?")
                    ],
                    highlights=[
                        CardHighlight(title="주문 마감 임박", description=f"{order_deadline_min}분 남음 · AI 추천안 3개 준비됨", tone="warning", delivery_scheduled=True),
                        CardHighlight(title="주문 누락 방지가 목적입니다", description="최종 결정은 점주님이 하십니다.", tone="info")
                    ],
                    metrics=[
                        CardMetric(label="주문 상태", value="검토 필요", tone="default"),
                        CardMetric(label="AI 추천안", value="3개 준비됨", tone="primary"),
                        CardMetric(label="추천 기준", value="전일 / 전주 / 패턴", tone="default")
                    ]
                ),
                DashboardCard(
                    domain="sales",
                    title="손익 분석",
                    description="순이익 및 손익분기점 분석",
                    cta_label="손익분석 상세보기",
                    cta_path="/sales",
                    prompts=[
                        PromptAction(id="sales-1", label="오늘 순이익은?", prompt="오늘 순이익은?"),
                        PromptAction(id="sales-2", label="손익분기점은?", prompt="손익분기점은?"),
                        PromptAction(id="sales-3", label="어제와 비교하면?", prompt="어제와 비교하면?")
                    ],
                    highlights=[
                        CardHighlight(title="오늘 순이익", description="+342,000원 · 순이익률 18.5%", tone="success"),
                        CardHighlight(title="손익분기점 달성 · +230,000원 초과", description="매장 운영 패턴과 최근 성과를 반영한 답변을 제공합니다.", tone="info")
                    ],
                    metrics=[
                        CardMetric(label="매출", value="1,850,000원", tone="default"),
                        CardMetric(label="원가", value="-890,000원", tone="danger"),
                        CardMetric(label="인건비", value="-520,000원", tone="danger"),
                        CardMetric(label="기타 비용", value="-98,000원", tone="danger")
                    ]
                )
            ]
        )

        # --- C. Dashboard Insights (하단) ---
        insights_data = DashboardInsightsResponse(
            insights=[
                InsightItem(id="weekend-demand", description="주말 매출이 평일 대비 40% 높습니다. 주말 생산량 선반영이 필요합니다.", evidence_sources=["최근 4주 요일별 매출 트렌드"]),
                InsightItem(id="top-margin-item", description="초코 도넛 순이익률 18.9%로 최고 수익 품목입니다.", evidence_sources=["원가율 대비 판매가 분석"]),
                InsightItem(id="matcha-margin", description="말차 도넛 원가율 개선 필요. 현재 순이익률 17.9%입니다.", evidence_sources=["원가 변동 및 마진 분석"])
            ],
            quick_links=[
                QuickLink(label="생산 관리", path="/production"),
                QuickLink(label="주문 관리", path="/ordering"),
                QuickLink(label="손익 분석", path="/sales")
            ]
        )

        return HomeDashboardResponse(
            target_date=target_date,
            store_id=store_id,
            overview=overview_data,
            cards=cards_data,
            insights=insights_data
        )
