from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --- 공통 열거형 (Enums) ---

class RiskLevel(str, Enum):
    SAFE = "정상"
    CAUTION = "주의"
    WARNING = "경고"
    DANGER = "위험"
    CRITICAL = "즉시생산"

# ... (기존 모델들 유지)

# --- 신규 추가: FE 연동 시뮬레이션 및 ROI 분석 규격 ---

class SimulationRequest(BaseModel):
    """시뮬레이션 분석 요청"""
    store_id: str
    item_id: str
    simulation_date: str  # YYYY-MM-DD
    lead_time_hour: int = 1
    margin_rate: float = 0.3


class SimulationFullRequest(SimulationRequest):
    """백엔드에서 DB 데이터를 포함해 전달하는 시뮬레이션 요청"""
    inventory_data: List[Dict[str, Any]] = Field(default_factory=list)
    production_data: List[Dict[str, Any]] = Field(default_factory=list)
    sales_data: List[Dict[str, Any]] = Field(default_factory=list)

class ChartDataPoint(BaseModel):
    """시계열 차트 데이터 포인트"""
    time: str
    actual_stock: float
    ai_guided_stock: float

class SimulationSummary(BaseModel):
    """시뮬레이션 요약 지표"""
    additional_sales_qty: float
    additional_profit_amt: int
    additional_waste_qty: float
    additional_waste_cost: int
    net_profit_change: int
    performance_status: str # "POSITIVE" | "NEGATIVE"
    chance_loss_reduction: Optional[float] = Field(None, description="AI 예측 생산으로 회복 가능한 찬스로스 금액(원)")

class SimulationReportResponse(BaseModel):
    """최종 시뮬레이션 리포트 응답"""
    metadata: Dict[str, Any]
    summary_metrics: SimulationSummary
    time_series_data: List[ChartDataPoint]
    actions_timeline: List[str]

class ProductionPattern(BaseModel):
    time: str
    qty: int

class SKUProductionStatus(BaseModel):
    item_cd: str
    item_nm: str
    status: str
    current_qty: int
    predict_1h_qty: int
    avg_4w_prod_1st: ProductionQtyPattern | None
    avg_4w_prod_2nd: ProductionQtyPattern | None
    chance_loss_reduction_pct: int
    sales_velocity: float
    tags: list[str]
    alert_message: str
    can_produce: bool

class ProductionDashboardSummary(BaseModel):
    critical_count: int
    warning_count: int
    safe_count: int
    avg_chance_loss_reduction: float

class ProductionDashboardResponse(BaseModel):
    store_id: str
    summary: ProductionDashboardSummary
    sku_list: List[SKUProductionStatus]

    chart_data: List[ChartDataPoint]
    action_timeline: List[str]


class OrderOptionType(str, Enum):
    LAST_WEEK = "전주 동요일 주문 그대로"
    TWO_WEEKS_AGO = "전전주 동요일 주문 그대로"
    LAST_MONTH = "전월 동요일 주문 그대로"
    SPECIAL = "특별 기간(예: 전년 추석) 주문 추천"


# --- 1. 생산 관리 Agent (Production Management) ---

class ProductionPattern(BaseModel):
    """4주 평균 생산/판매 패턴 데이터"""
    day_of_week: str = Field(..., description="요일")
    hour: int = Field(..., description="시간대 (0-23)")
    avg_sales_qty: float = Field(..., description="평균 판매량")


class ProductionStatusRequest(BaseModel):
    """실시간 재고 현황 및 예측 요청"""
    store_id: str
    sku_code: str
    sku_name: str
    current_stock: int = Field(..., description="현재고")
    predicted_stock_1h: float = Field(..., description="1시간 후 예상 재고")
    pattern_4w: List[ProductionPattern] = Field(default_factory=list, description="4주 평균 패턴")
    is_campaign: bool = False


class ProductionAlarmResponse(BaseModel):
    """점주용 생산 알림 메시지 응답"""
    sku_name: str
    risk_status: RiskLevel
    message: str = Field(..., description="AI가 생성한 점주용 알림 문구")
    predicted_stockout_time: Optional[str] = Field(None, description="예상 품절 시점 (HH:mm)")
    suggested_production_qty: int = Field(0, description="권장 생산 수량")
    chance_loss_prevented: Optional[int] = Field(None, description="예상 찬스 로스 방지 금액(원)")


# --- 2. 주문 관리 Agent (Ordering Management) ---

class OrderingOption(BaseModel):
    """주문 추천 옵션 상세"""
    option_type: OrderOptionType
    recommended_qty: int
    reasoning: str = Field(..., description="해당 옵션 추천 근거 (이벤트, 날씨, 휴일 등 반영)")
    expected_sales: int = Field(..., description="기대 판매량")
    seasonality_weight: Optional[float] = Field(None, description="캠페인 마스터 기반 시즌성 가중치 (1.0 초과 시 표시)")
    option_id: Optional[str] = Field(None, description="프론트/백엔드 연동용 옵션 식별자")
    title: Optional[str] = Field(None, description="옵션 카드 제목")
    basis: Optional[str] = Field(None, description="산정 기준 요약")
    description: Optional[str] = Field(None, description="옵션 카드 설명")
    recommended: Optional[bool] = Field(None, description="AI 추천 우선 옵션 여부")
    reasoning_text: Optional[str] = Field(None, description="프론트 계약용 추천 근거 문장")
    reasoning_metrics: List[Dict[str, str]] = Field(default_factory=list, description="프론트 계약용 근거 지표")
    special_factors: List[str] = Field(default_factory=list, description="프론트 계약용 특이사항")
    items: List[Dict[str, Any]] = Field(default_factory=list, description="프론트 계약용 품목별 주문 라인")


class OrderingRecommendationRequest(BaseModel):
    """주문 마감 전 추천 요청"""
    store_id: str
    target_date: str = Field(..., description="주문 대상 일자 (YYYY-MM-DD)")
    current_context: Dict[str, Any] = Field(..., description="날씨, 캠페인, 공휴일, 인근 이벤트 정보")
    recent_stock_trends: List[Dict[str, Any]] = Field(default_factory=list, description="최근 재고 흐름")


class OrderingRecommendationResponse(BaseModel):
    """3가지 주문 옵션 추천 응답"""
    store_id: str
    recommendations: List[OrderingOption] = Field(..., max_items=4, min_items=3)
    summary_insight: str = Field(..., description="전체적인 주문 전략 제언")
    deadline_minutes: Optional[int] = Field(None, description="주문 마감까지 남은 시간(분)")
    deadline_at: Optional[str] = Field(None, description="주문 마감 시각(HH:MM)")
    purpose_text: Optional[str] = Field(None, description="주문 화면 목적 안내 문구")
    caution_text: Optional[str] = Field(None, description="최종 결정 안내 문구")
    weather_summary: Optional[str] = Field(None, description="날씨 요약")
    trend_summary: Optional[str] = Field(None, description="최근 주문/재고 추세 요약")
    business_date: Optional[str] = Field(None, description="기준 영업일")


# --- 3. 매출 분석 Agent (Sales Analysis) ---

class SalesInsight(BaseModel):
    """매출 분석 결과 및 인사이트"""
    text: str = Field(..., description="자연어 분석 요약")
    evidence: List[str] = Field(..., description="분석 근거 데이터 포인트 (전주 대비, 채널 비중 등)")
    actions: List[str] = Field(..., description="점주가 즉시 실행 가능한 액션 아이템")
    follow_up_questions: List[str] = Field(
        default_factory=list,
        description="골든쿼리 유도용 후속 예상 질문 3개",
    )


class SalesQueryRequest(BaseModel):
    """매출 관련 자연어 질의 요청"""
    store_id: str
    query: str = Field(..., description="점주 질의 (예: '어제 배달 비중이 왜 낮았지?')")
    domain: str | None = Field(None, description="질의 도메인 (production|ordering|sales)")
    business_date: str = Field(default="2026-03-05", description="기준 영업일")
    system_instruction: str | None = Field(None, description="동적 시스템 프롬프트")
    raw_data_context: list[dict[str, Any]] | None = Field(
        None, description="DB에서 조회된 관련 매출 통계 데이터"
    )


class SalesPromptSuggestRequest(BaseModel):
    """매장 컨텍스트 기반 추천 질문 생성 요청"""

    store_id: str
    domain: str = Field(default="sales", description="질문 도메인 (production|ordering|sales)")
    context_prompts: list[dict[str, str]] = Field(
        default_factory=list, description="백엔드가 생성한 데이터 기반 초안 질문"
    )
    system_instruction: str | None = Field(None, description="동적 시스템 프롬프트")


class SalesPromptItem(BaseModel):
    label: str
    category: str
    prompt: str


class SalesPromptSuggestResponse(BaseModel):
    store_id: str
    domain: str
    prompts: list[SalesPromptItem] = Field(default_factory=list)


class SalesQueryResponse(BaseModel):
    """매출 질의 응답"""
    answer: SalesInsight
    confidence_score: float = Field(1.0, description="응답 신뢰도 (0~1)")
    generated_at: datetime = Field(default_factory=datetime.now)
    source_data_period: str = Field(..., description="분석에 사용된 데이터 기간")
    channel_analysis: Optional[Dict[str, Any]] = Field(None, description="채널별 매출 비중 분석 데이터")
    profit_simulation: Optional[Dict[str, Any]] = Field(None, description="표준 마진 기반 수익성 시뮬레이션 데이터")
    data_lineage: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="AI가 생성 및 실행한 쿼리 히스토리 (투명성 검증용)")
    text: Optional[str] = Field("", description="answer.text 미러 필드")
    evidence: List[str] = Field(default_factory=list, description="answer.evidence 미러 필드")
    actions: List[str] = Field(default_factory=list, description="answer.actions 미러 필드")
    follow_up_questions: List[str] = Field(default_factory=list, description="후속 예상 질문 3개")
    query_type: Optional[str] = None
    processing_route: Optional[str] = None
    queried_period: Optional[Dict[str, Any]] = None
    grounding: Optional[Dict[str, Any]] = None
    overlap_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    masked_fields: List[str] = Field(default_factory=list)
    blocked: bool = False


# --- 공통 응답 구조 ---

class BaseResponse(BaseModel):
    success: bool = True
    message: str = "Success"
    data: Optional[Any] = None
    error_code: Optional[str] = None


# --- 생산 피드백 보정 로직 ---

class FeedbackRecord(BaseModel):
    store_id: str
    sku_id: str
    recommended_qty: float
    actual_qty: float
    recorded_at: Optional[str] = None


class FeedbackCorrectionResponse(BaseModel):
    store_id: str
    sku_id: str
    correction_factor: float
    message: str


# --- 현장 예외 상황 룰셋 ---

class ExceptionCheckRequest(BaseModel):
    sku_id: str
    recommended_qty: float
    store_closing_time: str  # "HH:MM" e.g. "22:00"
    current_time: Optional[str] = None  # "HH:MM", None이면 현재 시각
    avg_production_qty: Optional[float] = None


class ExceptionCheckResult(BaseModel):
    sku_id: str
    suppressed: bool
    requires_manual_review: bool
    reason: Optional[str] = None


# --- PUSH 알림 페이로드 ---

class PushNotificationPayload(BaseModel):
    title: str
    body: str
    sku_id: str
    store_id: str
    severity: str  # "high" | "medium" | "low"


class PushNotificationListResponse(BaseModel):
    store_id: str
    alerts: List[PushNotificationPayload]
    alert_count: int


# --- 주문 마감 알림 ---

class DeadlineAlertResponse(BaseModel):
    store_id: str
    deadline: str  # "HH:MM"
    minutes_remaining: int
    alert_level: str  # "urgent" | "normal" | "passed"
    message: str
    should_alert: bool
    notification_id: Optional[int] = None
    title: Optional[str] = None
    deadline_minutes: Optional[int] = None
    target_path: Optional[str] = None
    focus_option_id: Optional[str] = None
    target_roles: List[str] = Field(default_factory=list)


class DeadlineAlertBatchRequest(BaseModel):
    store_ids: List[str] = Field(default_factory=list, description="조회할 매장 ID 목록")


class DeadlineAlertBatchResponse(BaseModel):
    items: List[DeadlineAlertResponse] = Field(default_factory=list)


# --- 수익성 시뮬레이션 ---

class ProfitabilitySimulationRequest(BaseModel):
    store_id: str
    item_id: Optional[str] = None
    date_from: str
    date_to: str


class ProfitabilitySimulationResponse(BaseModel):
    store_id: str
    date_from: str
    date_to: str
    total_revenue: float
    estimated_margin_rate: float
    estimated_profit: float
    top_items: List[Dict[str, Any]]
    simulation_note: str


class MarketInsightItem(BaseModel):
    title: str
    description: str
    impact: Literal["high", "medium", "low"] = "medium"


class MarketRiskWarningItem(BaseModel):
    title: str
    description: str
    mitigation: str


class MarketActionItem(BaseModel):
    priority: int
    title: str
    action: str
    expected_effect: str


class MarketBranchScoreItem(BaseModel):
    store_id: str
    store_name: str
    growth_rate: str
    risk_level: Literal["high", "medium", "low"] = "medium"
    summary: str


class MarketInsightsRequest(BaseModel):
    audience: Literal["store_owner", "hq_admin"] = "store_owner"
    scope: Dict[str, Any] = Field(default_factory=dict)
    market_data: Dict[str, Any] = Field(default_factory=dict)
    branch_snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    store_name: str | None = None


class MarketInsightsResponse(BaseModel):
    executive_summary: str
    key_insights: List[MarketInsightItem] = Field(default_factory=list)
    risk_warnings: List[MarketRiskWarningItem] = Field(default_factory=list)
    action_plan: List[MarketActionItem] = Field(default_factory=list)
    branch_scoreboard: List[MarketBranchScoreItem] = Field(default_factory=list)
    report_markdown: str = ""
    evidence_refs: List[str] = Field(default_factory=list)
    audience: Literal["store_owner", "hq_admin"] = "store_owner"
    source: Literal["ai"] = "ai"
    trace_id: str | None = None


class OrderingHistoryInsightKpi(BaseModel):
    key: str
    label: str
    value: str
    tone: Literal["default", "primary", "warning", "danger", "success"] = "default"


class OrderingHistoryAnomalyItem(BaseModel):
    id: str
    severity: Literal["low", "medium", "high"] = "medium"
    kind: str
    message: str
    recommended_action: str
    related_items: List[str] = Field(default_factory=list)


class OrderingHistoryChangedItem(BaseModel):
    item_nm: str
    avg_ord_qty: float
    latest_ord_qty: int
    change_ratio: float


class OrderingHistoryInsightsRequest(BaseModel):
    store_id: str
    filters: Dict[str, Any] = Field(default_factory=dict)
    history_items: List[Dict[str, Any]] = Field(default_factory=list)
    summary_stats: Dict[str, Any] = Field(default_factory=dict)


class OrderingHistoryInsightsResponse(BaseModel):
    kpis: List[OrderingHistoryInsightKpi] = Field(default_factory=list)
    anomalies: List[OrderingHistoryAnomalyItem] = Field(default_factory=list)
    top_changed_items: List[OrderingHistoryChangedItem] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    retrieved_contexts: List[str] = Field(default_factory=list)
    confidence: float | None = None
    trace_id: str | None = None
