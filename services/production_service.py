from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
import pandas as pd

from schemas.contracts import (
    ProductionStatusRequest, 
    ProductionAlarmResponse, 
    RiskLevel,
    SimulationRequest,
    SimulationReportResponse,
    SimulationSummary,
    ChartDataPoint
)
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_production_alarm_prompt
from .production_agent import ProductionManagementAgent

logger = init_logger("production_service")

class ProductionService:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        # 데이터프레임들을 실제 서비스 시에는 전역 캐시나 DB에서 로드해와야 함
        self._agent_cache: Dict[str, ProductionManagementAgent] = {}

    def _get_agent(self, inventory_df: pd.DataFrame, production_df: pd.DataFrame, sales_df: pd.DataFrame) -> ProductionManagementAgent:
        """에이전트 인스턴스 생성 및 캐싱 (성능 최적화)"""
        # 간단한 구현을 위해 매번 생성하거나 필요시 캐싱 로직 추가
        return ProductionManagementAgent(inventory_df, production_df, sales_df)

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
        try:
            item_master = production_df[production_df['ITEM_CD'] == item_id].iloc[0]
            item_nm = str(item_master['ITEM_NM'])
            unit_price = int(item_master['SALE_PRC'])
            item_cost = int(item_master['ITEM_COST'])
        except:
            item_nm = "알 수 없는 상품"
            unit_price, item_cost = 1500, 700

        # 2. 시뮬레이션 루프 (Actual vs AI-Guided)
        sim_prod_df = production_df.copy()
        sim_sales_df = sales_df.copy()
        ai_actions_log = []
        
        # 요약 데이터 수집을 위한 변수
        actual_total_sales = float(sales_df[(sales_df['MASKED_STOR_CD'] == store_id) & (sales_df['ITEM_CD'] == item_id) & (sales_df['SALE_DT'] == target_date)]['SALE_QTY'].sum())
        
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
            actual_hr_qty = sales_df[(sales_df['MASKED_STOR_CD'] == store_id) & (sales_df['ITEM_CD'] == item_id) & (sales_df['SALE_DT'] == target_date) & (sales_df['TMZON_DIV'].astype(int) == hour)]['SALE_QTY'].sum()
            if actual_hr_qty == 0:
                potential = temp_agent.predictor.predict_next_hour_sales(store_id, item_id, sim_time, sales_df)
                if potential > 1.0:
                    new_sale = pd.DataFrame([{'MASKED_STOR_CD': store_id, 'ITEM_CD': item_id, 'SALE_DT': target_date, 'TMZON_DIV': str(hour).zfill(2), 'SALE_QTY': potential}])
                    sim_sales_df = pd.concat([sim_sales_df, new_sale], ignore_index=True)

        # 3. 수치 집계
        final_engine = agent.engine # 초기 엔진 사용 (재고 추이 추출용)
        flow_actual = final_engine.get_estimated_stock(store_id, item_id, target_date)
        flow_sim = ProductionManagementAgent(inventory_df, sim_prod_df, sim_sales_df).engine.get_estimated_stock(store_id, item_id, target_date)
        
        sim_total_sales = float(sim_sales_df[(sim_sales_df['MASKED_STOR_CD'] == store_id) & (sim_sales_df['ITEM_CD'] == item_id) & (sim_sales_df['SALE_DT'] == target_date)]['SALE_QTY'].sum())
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
                performance_status="POSITIVE" if (added_margin - added_waste_loss) > 0 else "NEGATIVE"
            ),
            chart_data=chart_data,
            action_timeline=ai_actions_log
        )
