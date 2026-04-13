from pydantic import BaseModel, Field
from typing import List, Optional

# ==========================================
# 1. Dashboard Overview (최상단 요약)
# ==========================================
class DashboardAction(BaseModel):
    id: str
    type: str  # "production" | "ordering" | "sales"
    urgency: str  # "urgent" | "important" | "recommended"
    badge_label: str
    title: str
    description: str
    cta_label: str
    cta_path: str
    focus_section: str
    related_sku_id: Optional[str] = None
    
    # [FE 추가 요청 사항 - RFP 반영용]
    ai_reasoning: Optional[str] = Field(None, description="[추가요청] AI 상세 분석 근거")
    is_finished_good: Optional[bool] = Field(False, description="[추가요청] 완제품 여부 (생산 버튼 비활성화용)")
    confidence_score: Optional[float] = Field(None, description="[추가요청] AI 분석 신뢰도 점수 (0~1.0)")

class DashboardStat(BaseModel):
    key: str
    label: str
    value: str
    tone: str  # "danger" | "primary" | "success" | "default"

class DashboardOverviewResponse(BaseModel):
    updated_at: str
    priority_actions: List[DashboardAction]
    stats: List[DashboardStat]

# ==========================================
# 2. Dashboard Cards (중단 3대 에이전트 카드)
# ==========================================
class PromptAction(BaseModel):
    id: str
    label: str
    prompt: str

class CardHighlight(BaseModel):
    title: str
    description: str
    tone: str  # "danger" | "warning" | "info" | "success" | "primary"
    
    # [FE 추가 요청 사항 - RFP 반영용]
    delivery_scheduled: Optional[bool] = Field(None, description="[추가요청-발주] 배송 예정일 여부")

class CardMetric(BaseModel):
    label: str
    value: str
    tone: str

class DashboardCard(BaseModel):
    domain: str  # "production" | "ordering" | "sales"
    title: str
    description: str
    cta_label: str
    cta_path: str
    prompts: List[PromptAction]
    highlights: List[CardHighlight]
    metrics: List[CardMetric]

class DashboardCardsResponse(BaseModel):
    cards: List[DashboardCard]

# ==========================================
# 3. Dashboard Insights (하단 주요 인사이트)
# ==========================================
class InsightItem(BaseModel):
    id: str
    description: str
    
    # [FE 추가 요청 사항 - RFP 반영용]
    evidence_sources: Optional[List[str]] = Field(None, description="[추가요청] 인사이트 도출 출처/근거 데이터")

class QuickLink(BaseModel):
    label: str
    path: str

class DashboardInsightsResponse(BaseModel):
    insights: List[InsightItem]
    quick_links: List[QuickLink]

# ==========================================
# [통합] Home Dashboard 종합 응답 모델
# ==========================================
class HomeDashboardResponse(BaseModel):
    """FE가 요청한 3가지 Mock 구조를 한 번에 반환하는 통합 모델"""
    target_date: str = Field(..., description="시연용 기준(과거) 일자")
    store_id: str = Field(..., description="시연용 기준 매장")
    overview: DashboardOverviewResponse
    cards: DashboardCardsResponse
    insights: DashboardInsightsResponse
