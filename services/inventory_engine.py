import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from common.logger import init_logger

logger = init_logger("inventory_engine")

class InventoryReversalEngine:
    """
    가이드라인 1: 재고 역산 엔진 (Core Logic)
    기초 재고, 생산(입고), 매출(출고) 데이터를 결합하여 가상 재고 흐름을 생성합니다.
    """
    def __init__(self, inventory_df: pd.DataFrame, production_df: pd.DataFrame, sales_df: pd.DataFrame):
        self.inventory_df = inventory_df
        self.production_df = production_df
        self.sales_df = sales_df

    def get_estimated_stock(self, store_cd: str, item_cd: str, target_date: str):
        """
        5분 단위 추정 재고 테이블 생성
        """
        logger.info(f"Calculating stock flow for Store: {store_cd}, Item: {item_cd}, Date: {target_date}")

        # 1. 기초값 설정 (STOCK_DT 기준 기초 재고)
        # SPL_DAY_STOCK_DTL 테이블에서 해당 일자의 기초 재고를 가져옵니다.
        # 가이드라인에 따라 당일 00시 기준 재고로 활용
        base_stock_row = self.inventory_df[
            (self.inventory_df['MASKED_STOR_CD'] == store_cd) & 
            (self.inventory_df['ITEM_CD'] == item_cd) &
            (self.inventory_df['STOCK_DT'] == target_date)
        ]
        
        # 가이드라인: STOCK_QTY 활용
        base_stock = base_stock_row['STOCK_QTY'].sum() if not base_stock_row.empty else 0

        # 2. 생산(입고) 데이터: PROD_DTL
        prod_data = self.production_df[
            (self.production_df['MASKED_STOR_CD'] == store_cd) & 
            (self.production_df['ITEM_CD'] == item_cd) &
            (self.production_df['PROD_DT'] == target_date)
        ].copy()
        
        # PROD_DGRE(차수)를 시간으로 변환 (예: 1차=08시, 2차=10시 등)
        def map_prod_time(dgre):
            try:
                hour = 8 + (int(dgre) - 1) * 2 # 단순 매핑
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=9)

        if not prod_data.empty:
            prod_data['timestamp'] = prod_data['PROD_DGRE'].apply(map_prod_time)

        # 3. 매출(출고) 데이터: DAILY_STOR_ITEM_TMZON
        sales_data = self.sales_df[
            (self.sales_df['MASKED_STOR_CD'] == store_cd) & 
            (self.sales_df['ITEM_CD'] == item_cd) &
            (self.sales_df['SALE_DT'] == target_date)
        ].copy()
        
        # TMZON_DIV (시간대)를 시간으로 변환
        def map_sale_time(tmzon):
            try:
                hour = int(tmzon)
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, '%Y%m%d')

        if not sales_data.empty:
            sales_data['timestamp'] = sales_data['TMZON_DIV'].apply(map_sale_time)

        # 4. 5분 단위 타임라인 구성 및 Running Total 계산
        start_time = datetime.strptime(target_date, '%Y%m%d')
        end_time = start_time + timedelta(days=1)
        timeline = pd.date_range(start=start_time, end=end_time, freq='5min', inclusive='left')
        
        df_flow = pd.DataFrame(index=timeline)
        df_flow['in_qty'] = 0.0
        df_flow['out_qty'] = 0.0

        # 생산 반영
        for _, row in prod_data.iterrows():
            ts = row['timestamp']
            if ts in df_flow.index:
                df_flow.at[ts, 'in_qty'] += row['PROD_QTY']

        # 매출 반영 (시간대별 매출을 5분 단위로 리샘플링/배분)
        for _, row in sales_data.iterrows():
            ts = row['timestamp']
            # 해당 시간(1시간) 동안 5분마다 균등하게 판매되었다고 가정 (1/12씩)
            for i in range(12):
                slot = ts + timedelta(minutes=i*5)
                if slot in df_flow.index:
                    df_flow.at[slot, 'out_qty'] += (row['SALE_QTY'] / 12)

        # 누적 재고 계산
        df_flow['stock_change'] = df_flow['in_qty'] - df_flow['out_qty']
        df_flow['estimated_stock'] = base_stock + df_flow['stock_change'].cumsum()

        return df_flow

    def estimate_inventory_5min(
        self,
        initial_stock: float,
        production_df: pd.DataFrame,
        sales_df: pd.DataFrame,
        target_time: datetime,
    ) -> pd.DataFrame:
        """
        기초재고 + 생산량 - 매출량 기반 5분 단위 재고 역산.
        production_df / sales_df: timestamp(datetime), qty(float) 컬럼을 가진 범용 DataFrame.
        """
        logger.info(f"estimate_inventory_5min: initial_stock={initial_stock}, target_time={target_time}")

        day_start = target_time.replace(hour=0, minute=0, second=0, microsecond=0)
        timeline = pd.date_range(start=day_start, end=target_time, freq='5min', inclusive='both')

        df_flow = pd.DataFrame(index=timeline)
        df_flow['in_qty'] = 0.0
        df_flow['out_qty'] = 0.0

        for _, row in production_df.iterrows():
            slot = pd.Timestamp(row['timestamp']).floor('5min')
            if slot in df_flow.index:
                df_flow.at[slot, 'in_qty'] += float(row['qty'])

        for _, row in sales_df.iterrows():
            slot = pd.Timestamp(row['timestamp']).floor('5min')
            if slot in df_flow.index:
                df_flow.at[slot, 'out_qty'] += float(row['qty'])

        df_flow['stock_change'] = df_flow['in_qty'] - df_flow['out_qty']
        df_flow['estimated_stock'] = initial_stock + df_flow['stock_change'].cumsum()

        return df_flow
