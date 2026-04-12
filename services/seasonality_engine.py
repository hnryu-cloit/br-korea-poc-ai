from __future__ import annotations

import logging
from typing import Dict, Optional
import pandas as pd

logger = logging.getLogger(__name__)


class SeasonalityEngine:
    """
    시즌성 가중치 엔진.
    1순위: 캠페인 마스터 기간 조회 → 캠페인 가중치 반환
    2순위: 역사적 판매 데이터 기반 요일별 가중치 (캠페인이 없는 날에 적용)
    캠페인도 없고 역사 데이터도 없으면 1.0 반환.
    """

    DEFAULT_WEIGHT = 1.0

    def __init__(self, campaign_df: pd.DataFrame, sales_df: pd.DataFrame = None):
        """
        :param campaign_df: 캠페인 마스터 DataFrame.
            필요 컬럼: start_date(str YYYY-MM-DD), end_date(str YYYY-MM-DD), weight(float, 선택).
        :param sales_df: 역사적 판매 DataFrame (DAILY_STOR_ITEM 구조).
            필요 컬럼: SALE_DT(str YYYYMMDD), SALE_QTY(numeric).
            제공 시 요일별 평균 판매량 기반 가중치를 2순위로 사용한다.
        """
        self.campaign_df = campaign_df.copy() if campaign_df is not None else pd.DataFrame()
        self._prepared = self._prepare_campaigns()
        self._dow_weights: Dict[int, float] = self._compute_dow_weights(sales_df)

    def _prepare_campaigns(self) -> pd.DataFrame:
        required = {'start_date', 'end_date'}
        if self.campaign_df.empty or not required.issubset(self.campaign_df.columns):
            return pd.DataFrame()

        df = self.campaign_df.copy()
        df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
        df['end_date'] = pd.to_datetime(df['end_date'], errors='coerce')
        df = df.dropna(subset=['start_date', 'end_date'])

        if 'weight' not in df.columns:
            df['weight'] = 1.2

        return df

    def _compute_dow_weights(self, sales_df: Optional[pd.DataFrame]) -> Dict[int, float]:
        """
        요일(0=월 ~ 6=일)별 평균 판매량을 전체 평균으로 나눈 상대 가중치를 계산한다.
        전체 평균 대비 판매가 많은 요일은 >1.0, 적은 요일은 <1.0 을 반환한다.
        데이터 없거나 계산 실패 시 빈 dict 반환 (→ fallback 1.0).
        """
        if sales_df is None or sales_df.empty:
            return {}
        if 'SALE_DT' not in sales_df.columns or 'SALE_QTY' not in sales_df.columns:
            return {}

        try:
            df = sales_df[['SALE_DT', 'SALE_QTY']].copy()
            df['SALE_QTY'] = pd.to_numeric(df['SALE_QTY'], errors='coerce').fillna(0)
            df['date'] = pd.to_datetime(df['SALE_DT'].astype(str), format='%Y%m%d', errors='coerce')
            df = df.dropna(subset=['date'])
            df['dow'] = df['date'].dt.dayofweek  # 0=월, 6=일

            dow_avg = df.groupby('dow')['SALE_QTY'].mean()
            overall_avg = dow_avg.mean()

            if overall_avg <= 0:
                return {}

            weights = {int(dow): round(float(avg / overall_avg), 4) for dow, avg in dow_avg.items()}
            logger.info("SeasonalityEngine: 요일별 가중치 산출 완료 %s", weights)
            return weights
        except Exception as e:
            logger.warning("SeasonalityEngine: 요일 가중치 계산 실패: %s", e)
            return {}

    def get_weight(self, target_date: str, item_id: str | None = None) -> float:
        """
        시즌성 가중치 반환.
        1순위: 캠페인 기간 해당 시 캠페인 가중치
        2순위: 역사적 요일별 가중치
        3순위: 1.0 (기본값)
        """
        try:
            ts = pd.Timestamp(target_date)
        except Exception:
            logger.warning("SeasonalityEngine: invalid target_date=%s", target_date)
            return self.DEFAULT_WEIGHT

        # 1순위: 캠페인 가중치
        if not self._prepared.empty:
            mask = (self._prepared['start_date'] <= ts) & (self._prepared['end_date'] >= ts)
            if item_id and 'item_id' in self._prepared.columns:
                item_mask = (self._prepared['item_id'].astype(str) == str(item_id)) | self._prepared['item_id'].isna()
                mask = mask & item_mask
            matched = self._prepared[mask]
            if not matched.empty:
                weight = float(matched['weight'].max())
                logger.info("SeasonalityEngine: campaign weight date=%s item=%s weight=%.3f", target_date, item_id, weight)
                return weight

        # 2순위: 요일별 역사적 가중치
        if self._dow_weights:
            dow = ts.dayofweek
            weight = self._dow_weights.get(dow, self.DEFAULT_WEIGHT)
            logger.info("SeasonalityEngine: dow weight date=%s dow=%d weight=%.3f", target_date, dow, weight)
            return weight

        return self.DEFAULT_WEIGHT

    def get_weights_range(self, start_date: str, end_date: str) -> dict[str, float]:
        """날짜 범위의 일별 가중치 dict 반환."""
        dates = pd.date_range(start=start_date, end=end_date, freq='D')
        return {d.strftime('%Y-%m-%d'): self.get_weight(d.strftime('%Y-%m-%d')) for d in dates}