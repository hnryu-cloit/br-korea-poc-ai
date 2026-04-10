from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import pandas as pd
import numpy as np

from .inventory_engine import InventoryReversalEngine
from .chance_loss_service import ChanceLossService
from .predictor import InventoryPredictor
from common.logger import init_logger

logger = init_logger("production_agent")

class ProductionManagementAgent:
    """
    [Production-Ready] 생산 관리 통합 에이전트
    - 재고 역산 엔진(InventoryReversalEngine) 연동
    - ML 기반 판매 예측(InventoryPredictor) 연동
    - 찬스로스 및 ROI 분석(ChanceLossService) 연동
    """
    def __init__(self, 
                 inventory_df: pd.DataFrame, 
                 production_df: pd.DataFrame, 
                 sales_df: pd.DataFrame, 
                 campaign_df: Optional[pd.DataFrame] = None):
        
        # 엔진 및 서비스 초기화
        self.engine = InventoryReversalEngine(inventory_df, production_df, sales_df)
        self.historical_sales_df = sales_df
        self.campaign_df = campaign_df if campaign_df is not None else pd.DataFrame()
        
        self.chance_loss_service = ChanceLossService(sales_df, self.campaign_df)
        self.predictor = InventoryPredictor()
        
        # 모델 학습 로직 제거 (외부 학습 스크립트에서 관리)
        # self.predictor.train(sales_df)

    def get_realtime_status(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        실시간 재고 상태 및 1시간/2시간 후 예측 정보를 조회합니다.
        """
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')
        
        # 1. 재고 역산 정보 조회
        try:
            stock_flow = self.engine.get_estimated_stock(store_cd, item_cd, target_date)
            current_idx = stock_flow.index.asof(now)
            current_stock = float(stock_flow.at[current_idx, 'estimated_stock']) if current_idx in stock_flow.index else 0.0
        except Exception as e:
            logger.error(f"Inventory calculation failed: {e}")
            current_stock = 0.0

        # 2. ML 기반 판매 예측
        pred_1h = self.predictor.predict_next_hour_sales(store_cd, item_cd, now, self.historical_sales_df)
        pred_2h_sum = pred_1h + self.predictor.predict_next_hour_sales(store_cd, item_cd, now + timedelta(hours=1), self.historical_sales_df)

        return {
            "current_stock": round(current_stock, 1),
            "predicted_sales_1h": round(pred_1h, 1),
            "predicted_sales_2h_total": round(pred_2h_sum, 1)
        }

    def generate_recommendation(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        생산 필요 여부를 판단하고 최종 추천 수량과 기대 이익을 산출합니다. (FE/BE 연동 핵심 로직)
        """
        now = current_time if current_time else datetime.now()
        status = self.get_realtime_status(store_cd, item_cd, item_nm, now)
        
        curr_stock = status["current_stock"]
        pred_2h = status["predicted_sales_2h_total"]
        
        # 생산 필요 판단 (현재고 < 2시간 예상 수요)
        need_production = bool(curr_stock < pred_2h)
        risk_level = "SAFE"
        if curr_stock <= 0: risk_level = "CRITICAL"
        elif need_production: risk_level = "WARNING"

        # 추천 수량 산출 (1개 단위 정밀 최적화)
        recommend_qty = 0
        if need_production:
            deficit = pred_2h - curr_stock
            recommend_qty = int(np.ceil(max(1, deficit)))

        # 기대 마진액 계산 (판가 1500원, 마진 30% 가정)
        unit_price = 1500
        margin_rate = 0.3
        expected_gain = int(pred_2h * unit_price * margin_rate) if need_production else 0

        # 과거 찬스로스 조회
        past_loss = self.chance_loss_service.calculate_chance_loss(store_cd, item_cd, now.strftime('%Y%m%d'), self.historical_sales_df)
        past_qty = past_loss.get("total_chance_loss_qty", 0)

        return {
            "timestamp": now.isoformat(),
            "item_info": {"item_cd": item_cd, "item_nm": item_nm},
            "inventory": {
                "current_qty": curr_stock,
                "status": risk_level
            },
            "prediction": status,
            "recommendation": {
                "need_production": need_production,
                "recommend_qty": recommend_qty,
                "expected_profit_gain": expected_gain,
                "past_chance_loss_qty": past_qty
            }
        }
