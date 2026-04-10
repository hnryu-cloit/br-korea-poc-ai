from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

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

class SimulationReportResponse(BaseModel):
    """최종 시뮬레이션 리포트 응답"""
    metadata: Dict[str, Any]
    summary_metrics: SimulationSummary
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


# --- 3. 매출 분석 Agent (Sales Analysis) ---

class SalesInsight(BaseModel):
    """매출 분석 결과 및 인사이트"""
    text: str = Field(..., description="자연어 분석 요약")
    evidence: List[str] = Field(..., description="분석 근거 데이터 포인트 (전주 대비, 채널 비중 등)")
    actions: List[str] = Field(..., description="점주가 즉시 실행 가능한 액션 아이템")


class SalesQueryRequest(BaseModel):
    """매출 관련 자연어 질의 요청"""
    store_id: str
    query: str = Field(..., description="점주 질의 (예: '어제 배달 비중이 왜 낮았지?')")
    raw_data_context: Optional[List[Dict[str, Any]]] = Field(None, description="DB에서 조회된 관련 매출 통계 데이터")


class SalesQueryResponse(BaseModel):
    """매출 질의 응답"""
    answer: SalesInsight
    generated_at: datetime = Field(default_factory=datetime.now)
    source_data_period: str = Field(..., description="분석에 사용된 데이터 기간")
    channel_analysis: Optional[Dict[str, Any]] = Field(None, description="채널별 매출 비중 분석 데이터")
    profit_simulation: Optional[Dict[str, Any]] = Field(None, description="표준 마진 기반 수익성 시뮬레이션 데이터")


# --- 공통 응답 구조 ---

class BaseResponse(BaseModel):
    success: bool = True
    message: str = "Success"
    data: Optional[Any] = None
    error_code: Optional[str] = None
