from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from common.logger import init_logger
from services.inventory_reversal_engine import InventoryReversalEngine
from services.inventory_predictor import InventoryPredictor
from services.chance_loss_engine import ChanceLossEngine

logger = init_logger("production_agent")


class ProductionManagementAgent:
    """
    생산 관리 통합 에이전트.
    - 재고 역산(InventoryReversalEngine) 연동
    - ML 기반 판매 예측(InventoryPredictor) 연동
    - 찬스로스 분석(ChanceLossService) 연동
    """

    def __init__(
        self,
        inventory_df: pd.DataFrame,
        production_df: pd.DataFrame,
        sales_df: pd.DataFrame,
        campaign_df: Optional[pd.DataFrame] = None,
        production_list_df: Optional[pd.DataFrame] = None,
    ):
        self.engine = InventoryReversalEngine(inventory_df, production_df, sales_df)
        self.historical_sales_df = sales_df
        self.campaign_df = campaign_df if campaign_df is not None else pd.DataFrame()
        self.production_list_df = production_list_df if production_list_df is not None else pd.DataFrame()

        self.chance_loss_engine = ChanceLossEngine()
        self.predictor = InventoryPredictor()

    def calculate_sales_velocity(self, store_cd: str, item_cd: str, target_date: str, current_time: datetime) -> float:
        """평소 4주 평균 대비 오늘의 판매 속도(배수) 계산"""
        if self.historical_sales_df.empty or 'MASKED_STOR_CD' not in self.historical_sales_df.columns:
            return 1.0

        current_hour = current_time.hour

        today_sales = self.historical_sales_df[
            (self.historical_sales_df['MASKED_STOR_CD'] == store_cd) &
            (self.historical_sales_df['ITEM_CD'] == item_cd) &
            (self.historical_sales_df['SALE_DT'] == target_date) &
            (self.historical_sales_df['TMZON_DIV'].astype(int) <= current_hour)
        ]['SALE_QTY'].sum()

        target_dt = datetime.strptime(target_date, '%Y%m%d')
        start_hist = target_dt - timedelta(weeks=4)
        target_weekday = target_dt.weekday()

        hist_data = self.historical_sales_df[
            (self.historical_sales_df['MASKED_STOR_CD'] == store_cd) &
            (self.historical_sales_df['ITEM_CD'] == item_cd)
        ].copy()

        if hist_data.empty:
            return 1.0

        hist_data['sale_dt_dt'] = pd.to_datetime(hist_data['SALE_DT'], format='%Y%m%d')
        hist_past = hist_data[
            (hist_data['sale_dt_dt'] >= start_hist) &
            (hist_data['sale_dt_dt'] < target_dt) &
            (hist_data['sale_dt_dt'].dt.weekday == target_weekday) &
            (hist_data['TMZON_DIV'].astype(int) <= current_hour)
        ]

        if hist_past.empty:
            return 1.0

        avg_past_sales = hist_past.groupby('SALE_DT')['SALE_QTY'].sum().mean()

        if avg_past_sales <= 0:
            return 1.0

        return round(float(today_sales / avg_past_sales), 2)

    def extract_production_pattern(self, store_cd: str, item_cd: str, target_date: str) -> dict:
        """과거 4주간의 주력 1차, 2차 생산 시간 및 수량 패턴 분석"""
        if self.engine.production_df.empty or 'MASKED_STOR_CD' not in self.engine.production_df.columns:
            return {"1st": None, "2nd": None}

        target_dt = datetime.strptime(target_date, '%Y%m%d')
        start_hist = target_dt - timedelta(weeks=4)

        hist_prod = self.engine.production_df[
            (self.engine.production_df['MASKED_STOR_CD'] == store_cd) &
            (self.engine.production_df['ITEM_CD'] == item_cd)
        ].copy()

        if hist_prod.empty:
            return {"1st": None, "2nd": None}

        hist_prod['prod_dt_dt'] = pd.to_datetime(hist_prod['PROD_DT'], format='%Y%m%d')
        hist_4w = hist_prod[(hist_prod['prod_dt_dt'] >= start_hist) & (hist_prod['prod_dt_dt'] < target_dt)]

        if hist_4w.empty:
            return {"1st": None, "2nd": None}

        pattern = hist_4w.groupby('PROD_DGRE')['PROD_QTY'].agg(['mean', 'count']).reset_index()
        pattern = pattern.sort_values(by='count', ascending=False)

        def dgre_to_time(dgre):
            try:
                hour = 8 + (int(dgre) - 1) * 2
                return f"{hour:02d}:00"
            except:
                return "08:00"

        result: Dict[str, Any] = {"1st": None, "2nd": None}
        if len(pattern) > 0:
            result["1st"] = {"time": dgre_to_time(pattern.iloc[0]['PROD_DGRE']), "qty": int(pattern.iloc[0]['mean'])}
        if len(pattern) > 1:
            result["2nd"] = {"time": dgre_to_time(pattern.iloc[1]['PROD_DGRE']), "qty": int(pattern.iloc[1]['mean'])}

        return result

    def get_realtime_status(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """실시간 재고 상태 및 1시간/2시간 후 예측 정보 조회"""
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')

        try:
            stock_flow = self.engine.get_estimated_stock(store_cd, item_cd, target_date)
            current_idx = stock_flow.index.asof(now)
            current_stock = float(stock_flow.at[current_idx, 'estimated_stock']) if current_idx in stock_flow.index else 0.0
        except Exception as e:
            logger.error(f"Inventory calculation failed: {e}")
            current_stock = 0.0

        pred_1h = self.predictor.predict_next_hour_sales(store_cd, item_cd, now, self.historical_sales_df)
        pred_2h_sum = pred_1h + self.predictor.predict_next_hour_sales(store_cd, item_cd, now + timedelta(hours=1), self.historical_sales_df)

        return {
            "current_stock": round(current_stock, 1),
            "predicted_sales_1h": round(pred_1h, 1),
            "predicted_sales_2h_total": round(pred_2h_sum, 1)
        }

    def generate_recommendation(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """생산 필요 여부 판단 및 패턴+예측 혼합형 추천 수량 산출"""
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')
        status = self.get_realtime_status(store_cd, item_cd, item_nm, now)

        curr_stock = status["current_stock"]
        pred_2h = status["predicted_sales_2h_total"]

        need_production = bool(curr_stock < pred_2h)

        is_production_item = True
        if not self.production_list_df.empty:
            is_production_item = not self.production_list_df[
                (self.production_list_df['MASKED_STOR_CD'] == store_cd) &
                (self.production_list_df['ITEM_CD'] == item_cd)
            ].empty

        if not is_production_item:
            need_production = False

        recommend_qty = 0
        reason_text = "현재고가 충분합니다."

        if need_production:
            ml_deficit = max(0, pred_2h - curr_stock)
            pattern = self.extract_production_pattern(store_cd, item_cd, target_date)
            pattern_qty = pattern["1st"]["qty"] if pattern.get("1st") else 0

            if pattern_qty > 0:
                blended_qty = (ml_deficit * 0.7) + (pattern_qty * 0.3)
                recommend_qty = int(np.ceil(blended_qty))
                reason_text = f"평소 생산량({pattern_qty}개)과 향후 예상 수요({int(pred_2h)}개)를 종합 고려하여 {recommend_qty}개 생산을 추천합니다."
            else:
                recommend_qty = int(np.ceil(ml_deficit))
                reason_text = f"향후 2시간 예상 수요 {int(pred_2h)}개에 맞춰 {recommend_qty}개 생산을 추천합니다."

        risk_level = "SAFE"
        if curr_stock <= 0:
            risk_level = "CRITICAL"
        elif need_production:
            risk_level = "WARNING"

        if not is_production_item:
            reason_text = "점포 생산 제외 품목(완제품)입니다. 생산 권고를 수행하지 않습니다."

        unit_price = 1500
        margin_rate = 0.3
        expected_gain = int(recommend_qty * unit_price * margin_rate) if need_production else 0

        past_loss = self.chance_loss_engine.estimate_chance_loss(
            self.historical_sales_df,
            self.engine.production_df,
            store_cd, item_cd, target_date,
            unit_price=1500,
        )
        past_qty = past_loss.get("estimated_loss_qty", 0)

        return {
            "timestamp": now.isoformat(),
            "item_info": {"item_cd": item_cd, "item_nm": item_nm},
            "inventory": {"current_qty": curr_stock, "status": risk_level},
            "prediction": status,
            "recommendation": {
                "need_production": need_production,
                "recommend_qty": recommend_qty,
                "reason": reason_text,
                "expected_profit_gain": expected_gain,
                "past_chance_loss_qty": past_qty
            }
        }

    def get_sku_status(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """대시보드 표출을 위한 개별 SKU의 종합 상태 정보 생성"""
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')

        rec = self.generate_recommendation(store_cd, item_cd, item_nm, now)
        current_qty = rec['inventory']['current_qty']
        predict_1h_qty = rec['prediction']['predicted_sales_1h']

        pattern = self.extract_production_pattern(store_cd, item_cd, target_date)

        can_produce = True
        if not self.production_list_df.empty:
            can_produce = not self.production_list_df[
                (self.production_list_df['MASKED_STOR_CD'] == store_cd) &
                (self.production_list_df['ITEM_CD'] == item_cd)
            ].empty

        velocity = self.calculate_sales_velocity(store_cd, item_cd, target_date, now)
        tags: List[str] = []
        alert_msg = "정상적인 판매 추이입니다."

        if not can_produce:
            tags.append("완제품")
            alert_msg = "본사 납품 완제품으로 매장 자체 생산이 불가능합니다."
        elif velocity >= 1.3:
            tags.append("속도↑")
            alert_msg = f"오늘 판매 속도가 평소 대비 {velocity}배 빠릅니다. 조기 품절 및 추가 생산 검토를 권장합니다."
        elif current_qty <= predict_1h_qty and current_qty > 0:
            tags.append("품절임박")
            alert_msg = "1시간 내 재고 소진이 예상됩니다."

        risk_map = {"CRITICAL": "위험", "WARNING": "주의", "SAFE": "안전"}
        status_kor = risk_map.get(rec['inventory']['status'], "안전")

        past_loss = rec['recommendation']['past_chance_loss_qty']
        reduction_pct = 0
        if past_loss > 0 and rec['recommendation']['need_production']:
            reduction_pct = int((past_loss * 0.8 / past_loss) * 100) if past_loss else 0
            if status_kor != "위험" and reduction_pct == 100:
                reduction_pct = 15

        return {
            "item_cd": item_cd,
            "item_nm": item_nm,
            "status": status_kor,
            "current_qty": int(current_qty),
            "predict_1h_qty": int(predict_1h_qty),
            "avg_4w_prod_1st": pattern.get("1st"),
            "avg_4w_prod_2nd": pattern.get("2nd"),
            "chance_loss_reduction_pct": reduction_pct,
            "sales_velocity": velocity,
            "tags": tags,
            "alert_message": alert_msg,
            "can_produce": can_produce
        }