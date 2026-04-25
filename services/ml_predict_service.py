from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

from services.inventory_predictor import InventoryPredictor

logger = logging.getLogger(__name__)


class MLPredictService:
    """ML 형식 재고 예측을 수행"""

    def __init__(self) -> None:
        self.predictor = InventoryPredictor()

    @staticmethod
    def _get_db_engine():
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc",
        )
        return create_engine(db_url, pool_pre_ping=True)

    def _fetch_stock_snapshot(self, store_id: str, sku: str) -> dict:
        """core_stock_rate에서 가장 최근 일자의 재고 스냅샷 조회"""
        engine = self._get_db_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT prc_dt, ord_avg, sal_avg, stk_avg, stk_rt, is_stockout
                    FROM core_stock_rate
                    WHERE masked_stor_cd = :store_id
                      AND item_cd        = :sku
                    ORDER BY prc_dt DESC
                    LIMIT 1
                    """
                ),
                {"store_id": store_id, "sku": sku},
            ).fetchone()
        if row is None:
            return {}
        return dict(row._mapping)

    def _fetch_recent_sales(self, store_id: str, sku: str, days: int = 7) -> list[dict]:
        """core_stock_rate에서 최근 N일 판매/재고 이력 조회"""
        engine = self._get_db_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT prc_dt, sal_avg, stk_avg, ord_avg
                    FROM core_stock_rate
                    WHERE masked_stor_cd = :store_id
                      AND item_cd        = :sku
                    ORDER BY prc_dt DESC
                    LIMIT :days
                    """
                ),
                {"store_id": store_id, "sku": sku, "days": days},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def _build_predictor_history_df(store_id: str, sku: str, history_rows: list[dict]) -> pd.DataFrame:
        """InventoryPredictor 입력 형식으로 최근 판매 이력을 변환"""
        rows: list[dict[str, object]] = []
        for row in history_rows:
            prc_dt = str(row.get("prc_dt") or "").strip()
            if len(prc_dt) != 8 or not prc_dt.isdigit():
                continue
            rows.append(
                {
                    "MASKED_STOR_CD": store_id,
                    "ITEM_CD": sku,
                    "SALE_DT": prc_dt,
                    "TMZON_DIV": 12,
                    "SALE_QTY": float(row.get("sal_avg") or 0.0),
                }
            )
        return pd.DataFrame(rows)

    def predict(self, store_id: str, sku: str) -> dict:
        """ML 모델 I/O 형식으로 1시간 후 재고를 예측"""
        snapshot = self._fetch_stock_snapshot(store_id, sku)
        history = self._fetch_recent_sales(store_id, sku, 7)

        if not snapshot:
            return {}

        current_stock = float(snapshot.get("stk_avg") or 0.0)
        predicted_sales_next_1h: float | None = None

        try:
            history_df = self._build_predictor_history_df(store_id, sku, history)
            predicted_sales_next_1h = float(
                self.predictor.predict_next_hour_sales(
                    store_id,
                    sku,
                    datetime.now(),
                    history_df,
                )
            )
            logger.info(
                "predict_ml_format: model prediction applied store_id=%s sku=%s sales_1h=%.2f",
                store_id,
                sku,
                predicted_sales_next_1h,
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.warning(
                "predict_ml_format: model prediction unavailable, fallback applied store_id=%s sku=%s error=%s",
                store_id,
                sku,
                exc,
            )

        if predicted_sales_next_1h is None:
            recent_sales = [float(r.get("sal_avg") or 0.0) for r in history]
            avg_sales = sum(recent_sales) / len(recent_sales) if recent_sales else 0.0
            predicted_sales_next_1h = round(avg_sales / 8.0, 1)

        predicted_stock_after_1h = round(max(current_stock - predicted_sales_next_1h, 0.0), 1)
        risk_detected = predicted_stock_after_1h <= max(1.0, current_stock * 0.3)
        last_updated = snapshot.get("prc_dt", datetime.now().strftime("%Y%m%d"))
        if len(last_updated) == 8:
            last_updated = f"{last_updated[:4]}-{last_updated[4:6]}-{last_updated[6:]} 00:00"

        return {
            "prediction_result": {
                "store_id": store_id,
                "sku": sku,
                "current_status": {
                    "current_stock": current_stock,
                    "last_updated": last_updated,
                },
                "prediction": {
                    "predicted_sales_next_1h": predicted_sales_next_1h,
                    "predicted_stock_after_1h": predicted_stock_after_1h,
                    "risk_detected": risk_detected,
                },
            }
        }
