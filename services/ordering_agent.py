import os
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy import create_engine, text
from common.logger import init_logger
from schemas.contracts import OrderingOption, OrderOptionType

logger = init_logger("ordering_agent")

class OrderingManagementAgent:
    """
    [Ordering-Ready] 주문 관리 핵심 에이전트 (Core Logic)
    과거 주문 이력 데이터를 DB에서 직접 조회하여 기본 3가지 옵션과 특별 옵션을 계산합니다.
    """
    def __init__(self, db_url: Optional[str] = None):
        default_db_url = "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc"
        self.db_url = os.getenv("DATABASE_URL", default_db_url)
        try:
            self.engine = create_engine(self.db_url)
            logger.info("OrderingManagementAgent DB 연결 성공.")
        except Exception as e:
            logger.error(f"OrderingManagementAgent DB 연결 실패: {e}")
            self.engine = None

    def get_product_group_deadlines(self) -> dict:
        """
        제품 그룹별 주문 마감 시간 설정 (원래 DataDataLoader에 있던 Mock DB 로직)
        """
        return {
            "도넛/완제품": "10:00",
            "냉동생지": "14:00",
            "커피/음료원부재료": "16:30",
            "포장재/기타": "18:00"
        }

    def _get_historical_qty(self, store_id: str, target_date_str: str, days_delta: int = 7, item_nm: str = None) -> int:
        """DB에서 특정 일자의 과거 주문 데이터를 계산합니다."""
        if not self.engine:
            return 0

        try:
            target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
            past_dt = target_dt - timedelta(days=days_delta)

            past_date_fmt1 = past_dt.strftime("%Y%m%d")
            past_date_fmt2 = past_dt.strftime("%Y-%m-%d")

            with self.engine.connect() as conn:
                # 1. 동일 지점, 동일 상품, 동일 과거 날짜
                query_exact = text("""
                    SELECT COALESCE(SUM(CAST("ORD_QTY" AS NUMERIC)), 0)
                    FROM "ORD_DTL"
                    WHERE "MASKED_STOR_CD" = :store_id
                      AND ("DLV_DT" = :date1 OR "DLV_DT" = :date2)
                      AND "ITEM_NM" = :item_nm
                """)
                exact_match = conn.execute(query_exact, {"store_id": store_id, "date1": past_date_fmt1, "date2": past_date_fmt2, "item_nm": item_nm}).scalar()
                
                if exact_match and float(exact_match) > 0:
                    return int(exact_match)

                # 2. 타 지점 동일 날짜 평균
                query_other_stores = text("""
                    SELECT COALESCE(AVG(total_qty), 0) FROM (
                        SELECT SUM(CAST("ORD_QTY" AS NUMERIC)) as total_qty
                        FROM "ORD_DTL"
                        WHERE ("DLV_DT" = :date1 OR "DLV_DT" = :date2)
                          AND "ITEM_NM" = :item_nm
                        GROUP BY "MASKED_STOR_CD"
                    ) sub
                """)
                other_avg = conn.execute(query_other_stores, {"date1": past_date_fmt1, "date2": past_date_fmt2, "item_nm": item_nm}).scalar()
                
                if other_avg and float(other_avg) > 0:
                    return int(float(other_avg))

                # 3. 타 상품 동일 지점 평균 (1.2배 가중치)
                query_store_avg = text("""
                    SELECT COALESCE(AVG(total_qty), 0) FROM (
                        SELECT SUM(CAST("ORD_QTY" AS NUMERIC)) as total_qty
                        FROM "ORD_DTL"
                        WHERE "MASKED_STOR_CD" = :store_id
                          AND ("DLV_DT" = :date1 OR "DLV_DT" = :date2)
                        GROUP BY "ITEM_NM"
                    ) sub
                """)
                store_avg = conn.execute(query_store_avg, {"store_id": store_id, "date1": past_date_fmt1, "date2": past_date_fmt2}).scalar()
                
                if store_avg and float(store_avg) > 0:
                    return int(float(store_avg) * 1.2)

        except Exception as e:
            logger.error(f"Error querying historical data: {e}")

        return 0

    def calculate_base_ordering_options(self, store_id: str, target_date: str, target_product: str = None) -> List[OrderingOption]:
        """기본 3가지 주문 옵션 산출"""
        qty_last_week = self._get_historical_qty(store_id, target_date, 7, target_product)
        qty_two_weeks = self._get_historical_qty(store_id, target_date, 14, target_product)
        qty_last_month = self._get_historical_qty(store_id, target_date, 28, target_product)

        if qty_last_week == 0 and qty_two_weeks == 0 and qty_last_month == 0:
            qty_last_week, qty_two_weeks, qty_last_month = 150, 145, 160

        return [
            OrderingOption(option_type=OrderOptionType.LAST_WEEK, recommended_qty=qty_last_week, reasoning="", expected_sales=qty_last_week),
            OrderingOption(option_type=OrderOptionType.TWO_WEEKS_AGO, recommended_qty=qty_two_weeks, reasoning="", expected_sales=qty_two_weeks),
            OrderingOption(option_type=OrderOptionType.LAST_MONTH, recommended_qty=qty_last_month, reasoning="", expected_sales=qty_last_month)
        ]

    def append_special_event_option_if_needed(self, store_id: str, target_date: str, target_product: str, context: dict, options: List[OrderingOption]):
        """특별 이벤트가 있을 경우 특별 옵션 추가"""
        special_event = context.get("special_event")
        if special_event:
            qty_special = self._get_historical_qty(store_id, target_date, 365, target_product)
            if qty_special == 0: 
                qty_special = int(options[0].recommended_qty * 1.5)
            options.append(
                OrderingOption(option_type=OrderOptionType.SPECIAL, recommended_qty=qty_special, reasoning="", expected_sales=qty_special)
            )
