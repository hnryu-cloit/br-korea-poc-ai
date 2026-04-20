from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from common.logger import init_logger

logger = init_logger(__name__)


class InventoryReversalEngine:
    """
    재고 역산 엔진 (Core Logic).
    기초 재고, 생산(입고), 매출(출고) 데이터를 결합하여 가상 재고 흐름을 생성합니다.
    """

    def __init__(
        self, inventory_df: pd.DataFrame, production_df: pd.DataFrame, sales_df: pd.DataFrame
    ):
        self.inventory_df = inventory_df
        self.production_df = production_df
        self.sales_df = sales_df

    def get_estimated_stock(self, store_cd: str, item_cd: str, target_date: str):
        """5분 단위 추정 재고 테이블 생성 (영업 시간 동적 추정 및 음수 보정)"""
        logger.info(
            f"Calculating stock flow for Store: {store_cd}, Item: {item_cd}, Date: {target_date}"
        )

        if not self.inventory_df.empty and "MASKED_STOR_CD" in self.inventory_df.columns:
            base_stock_row = self.inventory_df[
                (self.inventory_df["MASKED_STOR_CD"].astype(str) == str(store_cd))
                & (self.inventory_df["ITEM_CD"].astype(str) == str(item_cd))
                & (self.inventory_df["STOCK_DT"].astype(str) == str(target_date))
            ]
            base_stock = (
                pd.to_numeric(base_stock_row["STOCK_QTY"], errors="coerce").fillna(0).sum()
                if not base_stock_row.empty
                else 0
            )
        else:
            base_stock = 0

        if not self.production_df.empty and "MASKED_STOR_CD" in self.production_df.columns:
            prod_data = self.production_df[
                (self.production_df["MASKED_STOR_CD"].astype(str) == str(store_cd))
                & (self.production_df["ITEM_CD"].astype(str) == str(item_cd))
                & (self.production_df["PROD_DT"].astype(str) == str(target_date))
            ].copy()
        else:
            prod_data = pd.DataFrame()

        def map_prod_time(dgre):
            try:
                hour = 8 + (int(dgre) - 1) * 2
                return datetime.strptime(target_date, "%Y%m%d") + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, "%Y%m%d") + timedelta(hours=9)

        if not prod_data.empty:
            if "PROD_DGRE" in prod_data.columns:
                prod_data["timestamp"] = prod_data["PROD_DGRE"].apply(map_prod_time)
            else:
                prod_data["timestamp"] = datetime.strptime(target_date, "%Y%m%d") + timedelta(
                    hours=9
                )

        if not self.sales_df.empty and "MASKED_STOR_CD" in self.sales_df.columns:
            store_all_sales = self.sales_df[
                (self.sales_df["MASKED_STOR_CD"].astype(str) == str(store_cd))
                & (self.sales_df["SALE_DT"].astype(str) == str(target_date))
            ].copy()
        else:
            store_all_sales = pd.DataFrame()

        if not store_all_sales.empty and "TMZON_DIV" in store_all_sales.columns:
            store_all_sales["TMZON_DIV"] = (
                pd.to_numeric(store_all_sales["TMZON_DIV"], errors="coerce").fillna(-1).astype(int)
            )
            valid_sales = store_all_sales[store_all_sales["TMZON_DIV"] >= 0]

            if not valid_sales.empty:
                min_hour = valid_sales["TMZON_DIV"].min()
                max_hour = valid_sales["TMZON_DIV"].max() + 1
            else:
                min_hour, max_hour = 8, 23
        else:
            min_hour, max_hour = 8, 23

        if max_hour - min_hour < 6:
            min_hour = max(0, min_hour - 2)
            max_hour = min(24, max_hour + 2)

        if not store_all_sales.empty and "ITEM_CD" in store_all_sales.columns:
            sales_data = store_all_sales[
                store_all_sales["ITEM_CD"].astype(str) == str(item_cd)
            ].copy()
        else:
            sales_data = pd.DataFrame()

        def map_sale_time(tmzon):
            try:
                hour = int(tmzon)
                return datetime.strptime(target_date, "%Y%m%d") + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, "%Y%m%d")

        if not sales_data.empty:
            if "TMZON_DIV" in sales_data.columns:
                sales_data["timestamp"] = sales_data["TMZON_DIV"].apply(map_sale_time)
            else:
                sales_data["timestamp"] = datetime.strptime(target_date, "%Y%m%d") + timedelta(
                    hours=12
                )  # Default to noon if no time provided

        start_time = datetime.strptime(target_date, "%Y%m%d") + timedelta(hours=int(min_hour))
        end_time = datetime.strptime(target_date, "%Y%m%d") + timedelta(hours=int(max_hour))

        if end_time <= start_time:
            end_time = start_time + timedelta(hours=12)

        timeline = pd.date_range(start=start_time, end=end_time, freq="5min", inclusive="left")

        df_flow = pd.DataFrame(index=timeline)
        df_flow["in_qty"] = 0.0
        df_flow["out_qty"] = 0.0

        for _, row in prod_data.iterrows():
            ts = row["timestamp"]
            if ts in df_flow.index:
                qty = pd.to_numeric(row["PROD_QTY"], errors="coerce")
                if pd.notna(qty):
                    df_flow.at[ts, "in_qty"] += qty

        for _, row in sales_data.iterrows():
            ts = row["timestamp"]
            qty = pd.to_numeric(row["SALE_QTY"], errors="coerce")
            if pd.notna(qty):
                for i in range(12):
                    slot = ts + timedelta(minutes=i * 5)
                    if slot in df_flow.index:
                        df_flow.at[slot, "out_qty"] += qty / 12

        df_flow["stock_change"] = df_flow["in_qty"] - df_flow["out_qty"]

        current_stock = base_stock
        estimated_stocks = []

        for ts, row in df_flow.iterrows():
            change = row["stock_change"]
            next_stock = current_stock + change

            if next_stock < 0:
                shortage = abs(next_stock)
                virtual_prod_qty = int(np.ceil(shortage * 1.2))
                df_flow.at[ts, "in_qty"] += virtual_prod_qty
                change = df_flow.at[ts, "in_qty"] - df_flow.at[ts, "out_qty"]
                logger.debug(
                    f"[역추적 감지] {ts.strftime('%H:%M')} 재고 부족({next_stock:.1f}). 가상 생산량 {virtual_prod_qty}개 복원 주입."
                )
                current_stock += change
            else:
                current_stock = next_stock

            estimated_stocks.append(current_stock)

        df_flow["estimated_stock"] = estimated_stocks

        return df_flow
