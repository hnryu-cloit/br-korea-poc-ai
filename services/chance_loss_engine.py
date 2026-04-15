from __future__ import annotations

import logging
from typing import Any, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


class ChanceLossEngine:
    """
    매출 0 구간 탐지 기반 찬스로스 우회 산출 엔진.

    직접적인 품절 데이터 없이 '매출 0이지만 재고도 0인 시간대'를
    품절 의심 구간으로 판정하고, 인접 시간대 평균 매출로 손실 수량을 추정한다.
    """

    def estimate_chance_loss(
        self,
        sales_df: pd.DataFrame,
        production_df: pd.DataFrame,
        store_id: str,
        item_id: str,
        target_date: str,
        unit_price: float,
    ) -> Dict[str, Any]:
        """
        찬스로스를 정량 추정한다.

        반환:
            zero_sale_periods: 품절 의심 시간대 목록
            estimated_loss_qty: 추정 손실 수량
            estimated_loss_amount: 추정 찬스로스 금액 (원)
            confidence: "high" | "medium" | "low"
        """
        if sales_df.empty or 'MASKED_STOR_CD' not in sales_df.columns:
            return self._empty_result()

        store_sales = sales_df[
            (sales_df['MASKED_STOR_CD'] == store_id)
            & (sales_df['ITEM_CD'] == item_id)
            & (sales_df['SALE_DT'] == target_date)
        ].copy()

        if production_df.empty or 'MASKED_STOR_CD' not in production_df.columns:
            store_prod = pd.DataFrame()
        else:
            store_prod = production_df[
                (production_df['MASKED_STOR_CD'] == store_id)
                & (production_df['ITEM_CD'] == item_id)
                & (production_df['PROD_DT'] == target_date)
            ]

        if store_sales.empty:
            logger.info("찬스로스 산출: 매출 데이터 없음 (store=%s, item=%s, date=%s)", store_id, item_id, target_date)
            return self._empty_result()

        # 시간대별 매출 집계
        store_sales['hour'] = store_sales['TMZON_DIV'].astype(int)
        hourly = store_sales.groupby('hour')['SALE_QTY'].sum().reindex(range(8, 23), fill_value=0)

        # 영업 시간대 (8~22시) 중 매출 0 구간 탐지
        zero_hours = hourly[hourly == 0].index.tolist()

        # 생산이 전혀 없는 날은 단순 미영업으로 판단 → 신뢰도 낮게
        has_production = not store_prod.empty

        # 인접 시간대(앞뒤 2시간) 평균 매출로 손실 수량 추정
        zero_sale_periods: List[str] = []
        total_loss_qty = 0.0

        nonzero_avg = hourly[hourly > 0].mean() if (hourly > 0).any() else 0.0

        for h in zero_hours:
            neighbors = [hourly.get(h - 2, 0), hourly.get(h - 1, 0),
                         hourly.get(h + 1, 0), hourly.get(h + 2, 0)]
            neighbor_sales = [q for q in neighbors if q > 0]

            if not neighbor_sales:
                continue  # 인접 매출도 없으면 휴무 시간대로 간주

            adj_avg = sum(neighbor_sales) / len(neighbor_sales)
            if adj_avg < 0.5:
                continue  # 인접 평균이 너무 낮으면 품절 아님

            zero_sale_periods.append(f"{h:02d}:00")
            total_loss_qty += adj_avg

        estimated_loss_amount = round(total_loss_qty * unit_price, 0)

        # 신뢰도 산정
        data_coverage = (hourly > 0).sum() / len(hourly)
        if data_coverage >= 0.6 and has_production and len(zero_sale_periods) >= 1:
            confidence = "high"
        elif data_coverage >= 0.3 or has_production:
            confidence = "medium"
        else:
            confidence = "low"

        logger.info(
            "찬스로스 추정 완료: store=%s, item=%s, date=%s, loss_qty=%.1f, amount=%.0f, confidence=%s",
            store_id, item_id, target_date, total_loss_qty, estimated_loss_amount, confidence,
        )

        return {
            "zero_sale_periods": zero_sale_periods,
            "estimated_loss_qty": round(total_loss_qty, 1),
            "estimated_loss_amount": estimated_loss_amount,
            "confidence": confidence,
        }

    @staticmethod
    def _empty_result() -> Dict[str, Any]:
        return {
            "zero_sale_periods": [],
            "estimated_loss_qty": 0.0,
            "estimated_loss_amount": 0.0,
            "confidence": "low",
        }