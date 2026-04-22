from __future__ import annotations

import datetime
import os
import statistics
import json
from datetime import time as dt_time
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd

from schemas.management import ProductionPredictRequest, ProductionPredictResponse
from schemas.contracts import (
    ProductionStatusRequest,
    ProductionAlarmResponse,
    RiskLevel,
    SimulationRequest,
    SimulationReportResponse,
    SimulationSummary,
    ChartDataPoint,
    ProductionDashboardResponse,
    ProductionDashboardSummary,
    SKUProductionStatus,
    FeedbackCorrectionResponse,
    ExceptionCheckResult,
    PushNotificationPayload,
    PushNotificationListResponse,
)
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_production_alarm_prompt
from .grounded_workflow import GroundedWorkflow
from .production_agent import ProductionManagementAgent
from .sql_pipeline import SQLGenerator, QueryExecutor

logger = init_logger(__name__)

def normalize_payload_df(data: list[dict] | None) -> pd.DataFrame:
    """Helper to safely convert payload list of dicts to a pandas DataFrame."""
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)

from decimal import Decimal

# ... (기존 임포트 하단)

def _convert_decimal(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError

class ProductionService:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self._agent_cache: Dict[str, ProductionManagementAgent] = {}
        self._correction_factors: Dict[str, float] = {}
        self.sql_generator = SQLGenerator(gemini_client)
        self.query_executor = QueryExecutor()

    def analyze(self, payload: SalesQueryRequest) -> Dict[str, Any]:
        """자연어 질의를 받아 SQL 생성, 실행 후 분석 결과 반환 (Grounded Analysis)"""
        logger.info(f"ProductionService analyze: {payload.query}")
        workflow = GroundedWorkflow(self.gemini)
        result = workflow.run(query=payload.query, store_id=payload.store_id, domain="production")
        return result
        
        # 1. SQL 생성
        generated = self.sql_generator.generate(payload.query, payload.store_id, query_type="production")
        
        # 2. SQL 실행
        try:
            rows, columns = self.query_executor.run(generated.sql, payload.store_id)
        except Exception as e:
            logger.error(f"Production SQL execution failed: {e}")
            rows, columns = [], []
        # 3. 결과 기반 답변 생성 (Grounded) - 날짜 규칙 재강조
        prompt = f"""
        당신은 베이커리 매장의 생산 관리 전문가입니다.
        다음은 사용자의 질문에 대해 DB를 조회한 결과 데이터입니다.
        
        [질문]
        {payload.query}
        
        [조회 결과 (총 {len(rows)}건)]
        {serialized_rows}
        
        [절대 규칙 - 날짜 언급]
        - 오늘 날짜는 2026-03-10 입니다.
        - '최근 n일' 질문에 대한 답변 시, 반드시 오늘(3/10)을 제외하고 영업이 종료된 어제까지의 데이터임을 명시하세요.
        - 예: "어제(3/9)까지 최근 3일간..." 또는 "3월 7일부터 3월 9일까지..."
        
        [답변 가이드]
        - 데이터가 존재한다면 수치(생산량, 재고량 등)를 반드시 포함하여 구체적으로 답변하세요.
        - 조회 결과가 비어있다면([]), "해당 조건에 맞는 생산 데이터가 없습니다"라고 친절하게 안내하세요.
        - 'keywords' 배열에는 사용자의 질문에서 의도를 파악하기 위한 핵심 단어들을 나열하세요.
        - '근거' 배열에는 주요 수치를 포함하세요.
        
        [필수 응답 형식 (JSON)]
        {{
          "keywords": ["핵심단어1", "핵심단어2"],
          "text": "점주에게 보여줄 친절한 답변 텍스트",
          "evidence": ["조회된 수치 요약", "비교 결과 등"],
          "actions": ["재고 확인하기", "추가 생산 고려하기"]
        }}
        """
        
        try:
            res_raw = self.gemini.call_gemini_text(prompt, response_type="json")
            data = json.loads(res_raw) if isinstance(res_raw, str) else res_raw
        except Exception as e:
            logger.error(f"Grounded response generation failed: {e}")
            data = {"text": "데이터 조회는 완료되었으나 분석 중 오류가 발생했습니다.", "evidence": [], "actions": [], "keywords": []}

        return {
            "text": data.get("text", ""),
            "keywords": data.get("keywords", []),
            "evidence": data.get("evidence", []) + [f"조회 SQL: {generated.sql}"],
            "actions": data.get("actions", []),
            "query_type": "PRODUCTION",
            "processing_route": "production_service_grounded",
            "sql": generated.sql,
            "relevant_tables": generated.relevant_tables,
            "row_count": len(rows)
        }

    def _get_agent(
        self,
        inventory_df: pd.DataFrame,
        production_df: pd.DataFrame,
        sales_df: pd.DataFrame,
        store_prod_df: Optional[pd.DataFrame] = None,
    ) -> ProductionManagementAgent:
        """에이전트 인스턴스 생성 및 캐싱 (성능 최적화)"""
        # 간단한 구현을 위해 매번 생성하거나 필요시 캐싱 로직 추가
        if store_prod_df is None:
            store_prod_df = pd.DataFrame()
        return ProductionManagementAgent(inventory_df, production_df, sales_df, production_list_df=store_prod_df)

    def _call_ml_model(self, store_id: str, sku: str) -> dict | None:
        """외부 ML 모델 호출. ML_MODEL_URL 미설정 시 None 반환."""
        ml_url = os.environ.get("ML_MODEL_URL", "").rstrip("/")
        if not ml_url:
            return None
        token = os.environ.get("AI_SERVICE_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            resp = httpx.post(
                f"{ml_url}/predict",
                json={"store_id": store_id, "sku": sku},
                headers=headers,
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("ML 모델 호출 실패 (store_id=%s sku=%s): %s", store_id, sku, exc)
            return None

    def _map_ml_response(self, ml_result: dict, sku: str) -> ProductionPredictResponse | None:
        """ML 모델 응답을 ProductionPredictResponse로 변환."""
        try:
            pr = ml_result["prediction_result"]
            current_stock = float(pr["current_status"]["current_stock"])
            pred = pr["prediction"]
            predicted_stock_1h = float(pred["predicted_stock_after_1h"])
            risk_detected = bool(pred["risk_detected"])

            if risk_detected:
                stockout_expected_at = "1시간 이내"
                alert_message = "1시간 이내 품절 위험입니다. 즉시 생산 여부를 확인하세요."
            else:
                stockout_expected_at = None
                alert_message = "다음 1시간 재고는 안정 범위로 예상됩니다."

            std_dev = max(predicted_stock_1h * 0.15, 1.0)
            return ProductionPredictResponse(
                sku=sku,
                predicted_stock_1h=predicted_stock_1h,
                risk_detected=risk_detected,
                stockout_expected_at=stockout_expected_at,
                alert_message=alert_message,
                confidence=0.90,
                lower_bound=round(max(0.0, predicted_stock_1h - std_dev), 1),
                upper_bound=round(predicted_stock_1h + std_dev, 1),
                confidence_level="high",
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("ML 모델 응답 파싱 실패: %s | %s", exc, ml_result)
            return None

    def predict_stock(self, payload: ProductionPredictRequest) -> ProductionPredictResponse:
        """백엔드/AI 계약 호환용 1시간 후 재고 예측."""
        # ML 모델 우선 호출
        if payload.store_id:
            ml_result = self._call_ml_model(payload.store_id, payload.sku)
            if ml_result:
                mapped = self._map_ml_response(ml_result, payload.sku)
                if mapped:
                    return mapped

        if not payload.pattern_4w and not payload.history:
            raise ValueError(
                "예측에 필요한 데이터가 없습니다. pattern_4w 또는 history 중 하나 이상을 제공해야 합니다."
            )

        sales_values: list[float] = []
        production_values: list[float] = []
        stock_values: list[float] = []

        for row in payload.history:
            if not isinstance(row, dict):
                continue

            sales_value = row.get("sales", row.get("sale_qty"))
            production_value = row.get("production", row.get("prod_qty"))
            stock_value = row.get("stock", row.get("current_stock"))

            if sales_value is not None:
                sales_values.append(float(sales_value))
            if production_value is not None:
                production_values.append(float(production_value))
            if stock_value is not None:
                stock_values.append(float(stock_value))

        recent_sales = sum(sales_values[-3:]) / len(sales_values[-3:]) if sales_values else 0.0
        recent_production = sum(production_values[-3:]) / len(production_values[-3:]) if production_values else 0.0
        recent_stock = stock_values[-1] if stock_values else float(payload.current_stock)

        trend_adjustment = 0.0
        if payload.pattern_4w:
            trend_adjustment += float(payload.pattern_4w[0]) * 0.15
        if len(payload.pattern_4w) > 1:
            trend_adjustment += float(payload.pattern_4w[1]) * 0.1

        predicted_stock_1h = round(max(recent_stock + recent_production - recent_sales + trend_adjustment, 0.0), 1)
        risk_detected = payload.current_stock <= 0 or predicted_stock_1h <= max(1.0, payload.current_stock * 0.5)

        if risk_detected:
            stockout_expected_at = "1시간 이내" if recent_sales > recent_production else None
            alert_message = "1시간 이내 품절 위험입니다. 즉시 생산 여부를 확인하세요."
        else:
            stockout_expected_at = None
            alert_message = "다음 1시간 재고는 안정 범위로 예상됩니다."

        denominator = max(recent_stock + recent_production + recent_sales, 1.0)
        confidence = round(min(0.98, 0.6 + (recent_sales / denominator) * 0.3 + min(len(payload.history), 5) * 0.02), 2)

        # 1. ±1σ 신뢰구간 — 이력 부족 시 예측값 15% 적용
        if len(sales_values) >= 3:
            std_dev = statistics.stdev(sales_values[-6:]) if len(sales_values) >= 6 else statistics.stdev(sales_values)
        else:
            std_dev = predicted_stock_1h * 0.15
        std_dev = max(std_dev, 1.0)
        lower_bound = round(max(0.0, predicted_stock_1h - std_dev), 1)
        upper_bound = round(predicted_stock_1h + std_dev, 1)

        # 2. 신뢰 수준 문자열
        if confidence >= 0.85:
            confidence_level = "high"
        elif confidence >= 0.70:
            confidence_level = "medium"
        else:
            confidence_level = "low"

        return ProductionPredictResponse(
            sku=payload.sku,
            predicted_stock_1h=predicted_stock_1h,
            risk_detected=risk_detected,
            stockout_expected_at=stockout_expected_at,
            alert_message=alert_message,
            confidence=confidence,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            confidence_level=confidence_level,
        )

    def generate_production_guidance(self, query: str) -> str:
        """생산 질의에 대한 운영 가이드를 생성합니다."""
        prompt = (
            f"다음 생산 관련 질의에 답변하세요: {query}\n\n"
            "생산 관리 관점에서 재고 예측, 생산 타이밍, 위험 감지 정보를 포함해 한국어로 답변하세요."
        )
        try:
            return self.gemini.call_gemini_text(prompt)
        except (ValueError, TypeError, RuntimeError) as exc:
            logger.warning("생산 가이드 생성 실패: %s", exc)
            return "생산 관리 화면에서 SKU별 재고 현황과 1시간 후 예측값을 확인해주세요."

    def get_dashboard_summary(self, 
                              store_id: str, 
                              target_date: str,
                              inventory_df: pd.DataFrame, 
                              production_df: pd.DataFrame, 
                              sales_df: pd.DataFrame,
                              store_prod_df: pd.DataFrame) -> ProductionDashboardResponse:
        """
        [FE 연동] 매장 메인 대시보드 화면을 위한 전체 품목 상태 요약 데이터를 반환합니다.
        """
        agent = self._get_agent(inventory_df, production_df, sales_df, store_prod_df)
        
        # [수정] 해당 점포에서 취급하는 '모든 판매 가능 품목' 추출
        # store_prod_df (raw_stor_prod_item 뷰)에는 매장의 전체 취급 품목이 정의되어 있습니다.
        store_all_items = store_prod_df[store_prod_df['MASKED_STOR_CD'] == store_id]
        
        if not store_all_items.empty:
            unique_items = store_all_items['ITEM_CD'].unique()
            # 상품명 매핑 딕셔너리 생성
            item_nm_dict = dict(zip(store_all_items['ITEM_CD'], store_all_items['ITEM_NM']))
        else:
            # 만약 마스터 뷰가 누락되었다면, 과거 판매 이력과 재고 이력 전체에서 유니크 품목을 모두 긁어옵니다.
            store_inventory = inventory_df[inventory_df['MASKED_STOR_CD'] == store_id]
            store_sales = sales_df[sales_df['MASKED_STOR_CD'] == store_id]
            unique_items = list(set(store_inventory['ITEM_CD'].unique()) | set(store_sales['ITEM_CD'].unique()))
            item_nm_dict = {}
        
        sku_list = []
        critical_c = 0
        warning_c = 0
        safe_c = 0
        total_reduction = 0
        
        now = datetime.datetime.now()
        
        for item_cd in unique_items:
            # 상품명 안전 매핑
            item_nm = str(item_nm_dict.get(item_cd, f"상품 {item_cd}"))
                
            status_data = agent.get_sku_status(store_id, item_cd, item_nm, now)
            
            status_kor = status_data["status"]
            if status_kor == "위험": critical_c += 1
            elif status_kor == "주의": warning_c += 1
            else: safe_c += 1
            
            total_reduction += status_data["chance_loss_reduction_pct"]
            
            sku_list.append(SKUProductionStatus(**status_data))
            
        # 퍼센트 평균 계산
        avg_reduction = round(total_reduction / len(sku_list), 1) if len(sku_list) > 0 else 0.0

        summary = ProductionDashboardSummary(
            critical_count=critical_c,
            warning_count=warning_c,
            safe_count=safe_c,
            avg_chance_loss_reduction=avg_reduction
        )

        return ProductionDashboardResponse(
            store_id=store_id,
            summary=summary,
            sku_list=sku_list,
            chart_data=[],
            action_timeline=[]
        )

    def get_simulation_report(self, 
                              payload: SimulationRequest, 
                              inventory_df: pd.DataFrame, 
                              production_df: pd.DataFrame, 
                              sales_df: pd.DataFrame) -> SimulationReportResponse:
        """
        [FE 연동] 특정 날짜의 AI 가이드 시뮬레이션 리포트를 생성합니다.
        """
        agent = self._get_agent(inventory_df, production_df, sales_df)
        target_date = payload.simulation_date.replace("-", "")
        store_id = payload.store_id
        item_id = payload.item_id
        
        # 1. 마스터 정보 추출
        item_nm = "알 수 없는 상품"
        unit_price, item_cost = 1500, 700
        for df in [sales_df, production_df, inventory_df]:
            if not df.empty and 'ITEM_CD' in df.columns:
                matches = df[df['ITEM_CD'] == item_id]
                if not matches.empty:
                    if 'ITEM_NM' in df.columns and pd.notna(matches.iloc[0]['ITEM_NM']):
                        item_nm = str(matches.iloc[0]['ITEM_NM'])
                    if 'SALE_PRC' in df.columns and pd.notna(matches.iloc[0]['SALE_PRC']):
                        unit_price = int(matches.iloc[0]['SALE_PRC'])
                    if 'ITEM_COST' in df.columns and pd.notna(matches.iloc[0]['ITEM_COST']):
                        item_cost = int(matches.iloc[0]['ITEM_COST'])
                    
                    if item_nm != "알 수 없는 상품":
                        break

        # 2. 시뮬레이션 루프 (Actual vs AI-Guided)
        # AI 시뮬레이션에서는 '실제 당일 생산(추가생산 등)'을 무시하고,
        # 오직 8시(기초) 생산량만 남긴 뒤, 나머지는 AI가 알아서 판단하여 생산하도록 합니다.
        if not production_df.empty and 'PROD_DGRE' in production_df.columns:
            sim_prod_df = production_df[production_df['PROD_DGRE'].astype(str) == '1'].copy()
        else:
            sim_prod_df = production_df.copy()
            
        sim_sales_df = sales_df.copy()
        ai_actions_log = []

        # 요약 데이터 수집을 위한 변수
        if not sales_df.empty and 'MASKED_STOR_CD' in sales_df.columns:
            actual_total_sales = float(
                sales_df[
                    (sales_df['MASKED_STOR_CD'] == store_id) &
                    (sales_df['ITEM_CD'] == item_id) &
                    (sales_df['SALE_DT'] == target_date)
                ]['SALE_QTY'].sum()
            )
        else:
            actual_total_sales = 0.0
        
        for hour in range(8, 23):
            sim_time = datetime.datetime.strptime(target_date, "%Y%m%d").replace(hour=hour)
            # 현재까지의 가상 데이터로 에이전트 재생성 (동적 재고 반영)
            temp_agent = ProductionManagementAgent(inventory_df, sim_prod_df, sim_sales_df)
            rec = temp_agent.generate_recommendation(store_id, item_id, item_nm, sim_time)
            
            if rec['recommendation']['need_production']:
                added_qty = rec['recommendation']['recommend_qty']
                pseudo_dgre = str(int((hour - 8) / 2 + 1))
                new_prod = pd.DataFrame([{'MASKED_STOR_CD': store_id, 'ITEM_CD': item_id, 'PROD_DT': target_date, 'PROD_DGRE': pseudo_dgre, 'PROD_QTY': added_qty}])
                sim_prod_df = pd.concat([sim_prod_df, new_prod], ignore_index=True)
                ai_actions_log.append(f"[{hour:02d}:00] AI 추천으로 {added_qty}개 추가 생산")

            # 판매 회수 시뮬레이션
            if not sales_df.empty and 'MASKED_STOR_CD' in sales_df.columns:
                actual_hr_qty = sales_df[
                    (sales_df['MASKED_STOR_CD'] == store_id) &
                    (sales_df['ITEM_CD'] == item_id) &
                    (sales_df['SALE_DT'] == target_date) &
                    (sales_df['TMZON_DIV'].astype(str).str.lstrip('0').replace('', '0').astype(int) == hour)
                ]['SALE_QTY'].sum()
            else:
                actual_hr_qty = 0
            if actual_hr_qty == 0:
                potential = temp_agent.predictor.predict_next_hour_sales(store_id, item_id, sim_time, sales_df)
                if potential > 1.0:
                    new_sale = pd.DataFrame([{'MASKED_STOR_CD': store_id, 'ITEM_CD': item_id, 'SALE_DT': target_date, 'TMZON_DIV': str(hour).zfill(2), 'SALE_QTY': potential}])
                    sim_sales_df = pd.concat([sim_sales_df, new_sale], ignore_index=True)

        # 3. 수치 집계
        final_engine = agent.engine # 초기 엔진 사용 (재고 추이 추출용)
        flow_actual = final_engine.get_estimated_stock(store_id, item_id, target_date)
        flow_sim = ProductionManagementAgent(inventory_df, sim_prod_df, sim_sales_df).engine.get_estimated_stock(store_id, item_id, target_date)
        
        if not sim_sales_df.empty and 'MASKED_STOR_CD' in sim_sales_df.columns:
            sim_total_sales = float(sim_sales_df[(sim_sales_df['MASKED_STOR_CD'] == store_id) & (sim_sales_df['ITEM_CD'] == item_id) & (sim_sales_df['SALE_DT'] == target_date)]['SALE_QTY'].sum())
        else:
            sim_total_sales = 0.0
        closing_time = datetime.datetime.strptime(target_date, "%Y%m%d").replace(hour=23, minute=55)
        sim_waste = float(max(0, flow_sim.at[flow_sim.index.asof(closing_time), 'estimated_stock']))
        act_waste = float(max(0, flow_actual.at[flow_actual.index.asof(closing_time), 'estimated_stock']))
        
        recovered_qty = sim_total_sales - actual_total_sales
        added_margin = int(recovered_qty * unit_price * payload.margin_rate)
        added_waste_loss = int((sim_waste - act_waste) * item_cost)

        # 4. 차트 데이터 구성
        chart_data = []
        for h in range(8, 24, 2):
            t = datetime.datetime.strptime(target_date, "%Y%m%d").replace(hour=h)
            chart_data.append(ChartDataPoint(
                time=f"{h:02d}:00",
                actual_stock=round(float(flow_actual.at[flow_actual.index.asof(t), 'estimated_stock']), 1),
                ai_guided_stock=round(float(flow_sim.at[flow_sim.index.asof(t), 'estimated_stock']), 1)
            ))

        chance_loss_reduction = self.calculate_chance_loss_reduction(
            predicted_stock=flow_sim['estimated_stock'],
            actual_sales=flow_actual['out_qty'],
            unit_margin=unit_price * payload.margin_rate,
        )

        return SimulationReportResponse(
            metadata={
                "store_id": store_id, "item_id": item_id, "item_name": item_nm,
                "unit_price": unit_price, "item_cost": item_cost, "date": payload.simulation_date
            },
            summary_metrics=SimulationSummary(
                additional_sales_qty=round(recovered_qty, 1),
                additional_profit_amt=added_margin,
                additional_waste_qty=round(sim_waste - act_waste, 1),
                additional_waste_cost=added_waste_loss,
                net_profit_change=added_margin - added_waste_loss,
                performance_status="POSITIVE" if (added_margin - added_waste_loss) > 0 else "NEGATIVE",
                chance_loss_reduction=chance_loss_reduction,
            ),
            time_series_data=chart_data,
            actions_timeline=ai_actions_log
        )

    # --- 피드백 보정 로직 ---

    def apply_feedback_correction(
        self,
        store_id: str,
        sku_id: str,
        recommended_qty: float,
        actual_qty: float,
    ) -> FeedbackCorrectionResponse:
        """점주 실제 생산량 기반 예측 보정 계수 갱신 (EMA 방식)."""
        key = f"{store_id}:{sku_id}"
        ratio = actual_qty / recommended_qty if recommended_qty > 0 else 1.0
        old_factor = self._correction_factors.get(key, 1.0)
        new_factor = round(0.3 * ratio + 0.7 * old_factor, 4)
        self._correction_factors[key] = new_factor
        logger.info("피드백 보정 계수 갱신: key=%s, old=%.3f → new=%.3f", key, old_factor, new_factor)
        return FeedbackCorrectionResponse(
            store_id=store_id,
            sku_id=sku_id,
            correction_factor=new_factor,
            message=f"보정 계수 갱신 완료: {new_factor:.3f}",
        )

    def get_corrected_prediction(self, store_id: str, sku_id: str, base_prediction: float) -> float:
        """저장된 보정 계수를 적용한 예측값 반환."""
        key = f"{store_id}:{sku_id}"
        factor = self._correction_factors.get(key, 1.0)
        return round(base_prediction * factor, 1)

    # --- 현장 예외 룰셋 ---

    def check_production_exceptions(
        self,
        sku_id: str,
        recommended_qty: float,
        store_closing_time: str,
        current_time: Optional[str] = None,
        avg_production_qty: Optional[float] = None,
    ) -> ExceptionCheckResult:
        """마감 직전 억제 및 대량 주문 수동 검토 예외 규칙 적용."""
        closing_h, closing_m = map(int, store_closing_time.split(":"))
        if current_time:
            cur_h, cur_m = map(int, current_time.split(":"))
            now_t = dt_time(cur_h, cur_m)
        else:
            now_t = datetime.datetime.now().time()

        closing_minutes = closing_h * 60 + closing_m
        now_minutes = now_t.hour * 60 + now_t.minute

        # 규칙 1: 마감 30분 이내 생산 억제
        if 0 <= (closing_minutes - now_minutes) <= 30:
            return ExceptionCheckResult(
                sku_id=sku_id,
                suppressed=True,
                requires_manual_review=False,
                reason="마감 30분 이내 생산 억제",
            )

        # 규칙 2: 평균의 3배 초과 시 수동 검토 요청
        if avg_production_qty and avg_production_qty > 0 and recommended_qty > 3 * avg_production_qty:
            return ExceptionCheckResult(
                sku_id=sku_id,
                suppressed=False,
                requires_manual_review=True,
                reason=f"권장 수량({recommended_qty:.0f})이 평균({avg_production_qty:.0f})의 3배 초과",
            )

        return ExceptionCheckResult(sku_id=sku_id, suppressed=False, requires_manual_review=False)

    # --- PUSH 알림 페이로드 ---

    def get_push_notification_payloads(self, store_id: str) -> PushNotificationListResponse:
        """현재 위험 SKU 기반 PUSH 알림 페이로드 목록 반환."""
        alerts: List[PushNotificationPayload] = []
        # _agent_cache에 저장된 에이전트가 있으면 활용, 없으면 빈 목록 반환
        # (실시간 DB 연결 없는 POC 환경 기준)
        return PushNotificationListResponse(
            store_id=store_id,
            alerts=alerts,
            alert_count=len(alerts),
        )

    def calculate_chance_loss_reduction(
        self,
        predicted_stock: pd.Series,
        actual_sales: pd.Series,
        unit_margin: float,
    ) -> float:
        """AI 예측 생산으로 회복 가능한 찬스로스 금액 계산."""
        shortage_mask = predicted_stock < actual_sales
        chance_loss_qty = float((actual_sales[shortage_mask] - predicted_stock[shortage_mask]).sum())
        chance_loss_amount = round(chance_loss_qty * unit_margin, 2)
        logger.info(f"chance_loss_reduction: qty={chance_loss_qty:.1f}, margin={unit_margin}, amount={chance_loss_amount:.0f}")
        return chance_loss_amount


def normalize_payload_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """입력 payload 리스트를 DataFrame(대문자 컬럼)으로 정규화합니다."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df.columns = [str(column).upper() for column in df.columns]
    return df
