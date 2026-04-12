from __future__ import annotations

import logging
import pandas as pd

logger = logging.getLogger(__name__)


class SeasonalityEngine:
    """
    캠페인 마스터 기반 시즌성 가중치 엔진.
    캠페인 기간에 해당하는 날짜에 가중치(>1.0)를 반환하고, 해당 없으면 1.0을 반환한다.
    """

    DEFAULT_WEIGHT = 1.0

    def __init__(self, campaign_df: pd.DataFrame):
        """
        :param campaign_df: 캠페인 마스터 DataFrame.
            필요 컬럼: start_date(str YYYY-MM-DD), end_date(str YYYY-MM-DD), weight(float, 선택).
            컬럼이 없거나 빈 DataFrame이면 항상 기본값 1.0 반환.
        """
        self.campaign_df = campaign_df.copy() if campaign_df is not None else pd.DataFrame()
        self._prepared = self._prepare()

    def _prepare(self) -> pd.DataFrame:
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

    def get_weight(self, target_date: str, item_id: str | None = None) -> float:
        """캠페인 마스터 기반 시즌성 가중치 반환. 캠페인 없으면 1.0."""
        if self._prepared.empty:
            return self.DEFAULT_WEIGHT

        try:
            ts = pd.Timestamp(target_date)
        except Exception:
            logger.warning(f"SeasonalityEngine: invalid target_date={target_date}")
            return self.DEFAULT_WEIGHT

        mask = (self._prepared['start_date'] <= ts) & (self._prepared['end_date'] >= ts)

        if item_id and 'item_id' in self._prepared.columns:
            item_mask = (self._prepared['item_id'].astype(str) == str(item_id)) | self._prepared['item_id'].isna()
            mask = mask & item_mask

        matched = self._prepared[mask]
        if matched.empty:
            return self.DEFAULT_WEIGHT

        weight = float(matched['weight'].max())
        logger.info(f"SeasonalityEngine: date={target_date}, item={item_id}, weight={weight}")
        return weight

    def get_weights_range(self, start_date: str, end_date: str) -> dict[str, float]:
        """날짜 범위의 일별 가중치 dict 반환."""
        dates = pd.date_range(start=start_date, end=end_date, freq='D')
        return {d.strftime('%Y-%m-%d'): self.get_weight(d.strftime('%Y-%m-%d')) for d in dates}