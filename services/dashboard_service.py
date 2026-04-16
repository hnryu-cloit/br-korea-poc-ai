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
from services.sales_analyzer import SalesAnalyzer

logger = init_logger("dashboard_service")

class DashboardService:
    def __init__(self, prod_service: ProductionService, order_service: OrderingService, sales_analyzer: SalesAnalyzer):
        self.prod_service = prod_service
        self.order_service = order_service
        self.sales_service = sales_analyzer

    def get_home_overview(self, mock_payload: dict, raw_data: dict) -> HomeDashboardResponse:
        """
        [HOME] 3대 에이전트의 핵심 지표를 취합하여 FE가 요청한 통합 대시보드 데이터를 생성합니다.
        """
        store_id = mock_payload["store_id"]
        target_date = mock_payload["target_date"]
        current_time = mock_payload["current_time"]

        # 1. 생산 현황 (가장 위험한 품목 추출)
        inv_df = pd.DataFrame(raw_data.get("inventory_data", []), columns=['MASKED_STOR_CD', 'STOCK_DT', 'ITEM_CD', 'ITEM_NM', 'STOCK_QTY'] if not raw_data.get("inventory_data") else None)
        prod_df = pd.DataFrame(raw_data.get("production_data", []), columns=['MASKED_STOR_CD', 'PROD_DT', 'ITEM_CD', 'PROD_QTY'] if not raw_data.get("production_data") else None)
        sales_df = pd.DataFrame(raw_data.get("sales_data", []), columns=['MASKED_STOR_CD', 'SALE_DT', 'ITEM_CD', 'SALE_QTY', 'TMZON_DIV'] if not raw_data.get("sales_data") else None)
        store_prod_df = pd.DataFrame(raw_data.get("store_production_data", []))

        dash_res = None
        try:
            dash_res = self.prod_service.get_dashboard_summary(
                store_id, target_date, inv_df, prod_df, sales_df, store_prod_df
            )
        except Exception as e:
            logger.warning(f"생산 대시보드 조회 실패 - 빈 상태로 반환: {e}")
            dash_res = type('obj', (object,), {
                'summary': type('obj', (object,), {'critical_count': 0, 'avg_chance_loss_reduction': 0.0}),
                'sku_list': []
            })

        critical_count = getattr(dash_res.summary, 'critical_count', 0)
        avg_reduction = getattr(dash_res.summary, 'avg_chance_loss_reduction', 0.0)
        critical_items = [s for s in dash_res.sku_list if getattr(s, 'status', None) == "위험"]
        top_item = critical_items[0] if critical_items else None

        
        # 2. 주문 관리 정보 (Mocked for POC)
        order_deadline_min = 17

        # --- A. Dashboard Overview (상단) ---
        priority_actions = []

        # 위험 SKU가 있을 때만 생산 액션 추가
        if top_item is not None:
            priority_actions.append(DashboardAction(
                id="production-urgent-1",
                type="production",
                urgency="urgent" if getattr(top_item, 'status', '') == "위험" else "important",
                badge_label=f"긴급 - {getattr(top_item, 'alert_message', '생산 필요').split('.')[0]}",
                title=f"{getattr(top_item, 'item_nm', '상품')} 생산 필요",
                description=(
                    f"현재 {getattr(top_item, 'current_qty', '-')}개 → "
                    f"1시간 후 {getattr(top_item, 'predict_1h_qty', '-')}개 예상. "
                    f"지금 생산 시 찬스로스 {getattr(top_item, 'chance_loss_reduction_pct', 0)}% 감소 가능"
                ),
                cta_label="생산관리 상세보기",
                cta_path="/production",
                focus_section="risk-skus",
                related_sku_id=getattr(top_item, 'item_cd', ''),
                ai_reasoning=getattr(top_item, 'alert_message', ''),
                impact_metric=f"찬스로스 {getattr(top_item, 'chance_loss_reduction_pct', 0)}% 감소",
                is_finished_good=not getattr(top_item, 'can_produce', True),
                confidence_score=0.92
            ))
        elif critical_count == 0:
            priority_actions.append(DashboardAction(
                id="production-ok-1",
                type="production",
                urgency="recommended",
                badge_label="정상 - 생산 현황 점검 권장",
                title="생산 현황 확인",
                description="현재 위험 SKU가 감지되지 않았습니다. 생산 현황을 점검해 주세요.",
                cta_label="생산관리 상세보기",
                cta_path="/production",
                focus_section="overview",
                confidence_score=0.7
            ))

        priority_actions.append(DashboardAction(
            id="ordering-deadline-1",
            type="ordering",
            urgency="important",
            badge_label="중요 - 주문 마감 임박",
            title=f"주문 마감 {order_deadline_min}분 남음",
            description="오늘 주문 미완료 - AI 추천 3안 검토 후 점주가 직접 확정 필요",
            cta_label="주문 검토하기",
            cta_path="/ordering",
            focus_section="deadline",
            impact_metric="결품 방지율 98% 확보",
            confidence_score=0.88
        ))
        priority_actions.append(DashboardAction(
            id="sales-profit-1",
            type="sales",
            urgency="recommended",
            badge_label="권장 - 손익 확인",
            title="오늘 손익 확인 권장",
            description="손익분석 화면에서 실 데이터 기반 매출·순매출·상품별 분석을 확인하세요.",
            cta_label="손익분석 상세보기",
            cta_path="/sales",
            focus_section="profit-summary",
            confidence_score=0.85
        ))

        stats = [
            DashboardStat(key="production_risk_count", label="품절 위험 SKU", value=f"{critical_count}개", tone="danger" if critical_count > 0 else "default"),
            DashboardStat(key="ordering_deadline_minutes", label="주문 마감까지", value=f"{order_deadline_min}분", tone="primary"),
            DashboardStat(key="today_profit_estimate", label="오늘 순이익 추정", value="데이터 조회 필요", tone="default"),
            DashboardStat(key="alert_count", label="알림 상태", value=f"긴급 {critical_count}건" if critical_count > 0 else "정상", tone="danger" if critical_count > 0 else "default")
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
                    highlights=(
                        [
                            CardHighlight(
                                title=f"{getattr(top_item, 'item_nm', '상품')} 재고 소진 1시간 전",
                                description=(
                                    f"현재 재고 {getattr(top_item, 'current_qty', '-')}개 · "
                                    f"지금 생산 시 찬스 로스 {getattr(top_item, 'chance_loss_reduction_pct', 0)}% 감소 가능"
                                ),
                                tone="danger"
                            )
                        ] if top_item is not None else [
                            CardHighlight(title="위험 SKU 없음", description="현재 품절 위험 상품이 감지되지 않았습니다.", tone="info")
                        ]
                    ),
                    metrics=[
                        CardMetric(label="품절 위험", value=f"{critical_count}개", tone="danger" if critical_count > 0 else "default"),
                        CardMetric(label="찬스 로스 절감", value=f"{avg_reduction:.1f}%" if avg_reduction else "-", tone="primary")
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
                        CardHighlight(title="오늘 순이익 추정", description="손익 분석 화면에서 실 데이터 기반 분석을 확인하세요.", tone="info"),
                        CardHighlight(title="매출 인사이트 조회 가능", description="AI 질의로 채널별·상품별 매출 분석을 제공합니다.", tone="info")
                    ],
                    metrics=[
                        CardMetric(label="매출", value="손익분석 화면 참조", tone="default"),
                        CardMetric(label="순매출", value="손익분석 화면 참조", tone="default"),
                    ]
                )
            ]
        )

        # --- C. Dashboard Insights (하단) ---
        production_insight = (
            f"품절 위험 SKU {critical_count}개가 감지되었습니다. 생산 관리 화면에서 확인하세요."
            if critical_count > 0
            else "현재 품절 위험 SKU가 없습니다. 생산 현황을 정기적으로 점검하세요."
        )
        insights_data = DashboardInsightsResponse(
            insights=[
                InsightItem(id="production-status", description=production_insight, evidence_sources=["생산 예측 엔진 (실 DB 기반)"]),
                InsightItem(id="sales-query", description="손익 분석 화면에서 AI 질의로 상품별·채널별 매출 인사이트를 확인할 수 있습니다.", evidence_sources=["매출 분석 Agent"]),
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
