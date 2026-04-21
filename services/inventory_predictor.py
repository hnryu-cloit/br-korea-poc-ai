from __future__ import annotations

import os
import joblib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from common.logger import init_logger

try:
    import lightgbm as lgb
    HAS_LGB = True
except (ImportError, OSError):
    from sklearn.ensemble import RandomForestRegressor
    HAS_LGB = False

logger = init_logger(__name__)


class InventoryPredictor:
    """[Balanced High-Precision] 안정성과 정확도를 모두 잡은 최종 예측 엔진"""
    _instance = None
    _model_cache = None
    _stats_cache = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(InventoryPredictor, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, model_dir: Optional[str] = None):
        if self._initialized:
            return

        self.model = None
        if model_dir is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.model_dir = os.path.join(os.path.dirname(current_dir), "models")
        else:
            self.model_dir = model_dir

        self.model_path = os.path.join(self.model_dir, "inventory_lgbm_model.pkl")
        self.meta_path = os.path.join(self.model_dir, "model_meta.joblib")
        self.feature_cols = ['hour', 'weekday', 'is_weekend', 'lag_1h', 'lag_2h', 'rolling_mean_3h', 'store_avg', 'item_avg', 'hist_4w_avg']
        self.stats: Dict[str, Any] = {}
        self.meta_loaded: bool = False

        if InventoryPredictor._model_cache is not None:
            self.model = InventoryPredictor._model_cache
            self.stats = InventoryPredictor._stats_cache
            self.meta_loaded = True
            self._initialized = True
            logger.info("캐시된 예측 모델을 사용합니다.")
        else:
            if self.load_model():
                InventoryPredictor._model_cache = self.model
                InventoryPredictor._stats_cache = self.stats
                self._initialized = True

    def _prepare_training_data(self, history_df: pd.DataFrame, is_training: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        df = history_df.copy()

        df['SALE_QTY'] = pd.to_numeric(df['SALE_QTY'], errors='coerce').fillna(0)
        df['TMZON_DIV'] = pd.to_numeric(df['TMZON_DIV'], errors='coerce').fillna(0).astype(int)

        if is_training:
            # 0 판매 데이터 다운샘플링으로 클래스 불균형 보정 (비율 1:1.5)
            zero_sales = df[df['SALE_QTY'] == 0]
            non_zero_sales = df[df['SALE_QTY'] > 0]
            sample_size = min(len(zero_sales), int(len(non_zero_sales) * 1.5))
            zero_sales_sampled = zero_sales.sample(n=sample_size, random_state=42) if not zero_sales.empty else zero_sales
            df = pd.concat([non_zero_sales, zero_sales_sampled]).sort_values(['SALE_DT', 'TMZON_DIV'])

        # 날짜·시간 피처 생성
        df['sale_dt_dt'] = pd.to_datetime(df['SALE_DT'], format='%Y%m%d')
        df['hour'] = df['TMZON_DIV']
        df['weekday'] = df['sale_dt_dt'].dt.weekday
        df['is_weekend'] = (df['weekday'] >= 5).astype(int)

        df = df.sort_values(['MASKED_STOR_CD', 'ITEM_CD', 'SALE_DT', 'hour'])

        # 요일·시간대별 과거 판매 중앙값 통계 (hist_4w_avg 피처)
        hist_stats = df.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'weekday', 'hour'])['SALE_QTY'].median().reset_index()
        hist_stats.rename(columns={'SALE_QTY': 'hist_4w_avg'}, inplace=True)

        if is_training:
            self.stats['hist'] = hist_stats
            self.stats['store'] = df.groupby('MASKED_STOR_CD')['SALE_QTY'].median().to_dict()
            self.stats['item'] = df.groupby('ITEM_CD')['SALE_QTY'].median().to_dict()

        df = pd.merge(df, hist_stats, on=['MASKED_STOR_CD', 'ITEM_CD', 'weekday', 'hour'], how='left')

        # 시간 지연(lag) 피처 및 이동 중앙값
        group = df.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY']
        df['lag_1h'] = group.shift(1).fillna(0)
        df['lag_2h'] = group.shift(2).fillna(0)
        df['rolling_mean_3h'] = group.transform(lambda x: x.shift(1).rolling(3, min_periods=1).median()).fillna(0)

        df['store_avg'] = df['MASKED_STOR_CD'].map(self.stats.get('store', {})).fillna(0)
        df['item_avg'] = df['ITEM_CD'].map(self.stats.get('item', {})).fillna(0)

        if is_training:
            # 이상값 제거: 상위 5% 판매량 클리핑
            q_limit = df['SALE_QTY'].quantile(0.95)
            df = df[df['SALE_QTY'] <= q_limit]

        return df[self.feature_cols], df['SALE_QTY']

    def train(self, history_df: pd.DataFrame):
        """과거 판매 이력을 학습 데이터로 변환해 LightGBM(또는 RandomForest) 모델 학습"""
        X, y = self._prepare_training_data(history_df, is_training=True)
        if X.empty:
            logger.warning("학습할 데이터가 부족합니다.")
            return

        if HAS_LGB:
            params = {
                'objective': 'regression_l1', 'metric': 'mae', 'verbosity': -1, 'boosting_type': 'gbdt',
                'learning_rate': 0.01, 'num_leaves': 31, 'max_depth': 8, 'min_child_samples': 20,
                'feature_fraction': 0.7, 'n_jobs': -1
            }
            train_data = lgb.Dataset(X, label=y)
            self.model = lgb.train(params, train_data, num_boost_round=2000)
        else:
            self.model = RandomForestRegressor(n_estimators=200, max_depth=10, n_jobs=-1)
            self.model.fit(X, y)

        self.save_model()
        logger.info("중앙값 기반 MAE 최소화 모델 학습 완료.")

    def save_model(self):
        if not os.path.exists(self.model_dir):
            os.makedirs(self.model_dir)
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.stats, self.meta_path)

    def load_model(self) -> bool:
        if not os.path.exists(self.model_path):
            logger.warning("예측 모델 파일이 없습니다: %s", self.model_path)
            return False

        try:
            self.model = joblib.load(self.model_path)
        except Exception as exc:
            logger.exception("예측 모델 로드 실패: %s", exc)
            self.model = None
            self.stats = {}
            return False

        self.stats = {}
        self.meta_loaded = False
        if os.path.exists(self.meta_path):
            try:
                loaded_stats = joblib.load(self.meta_path)
                if not isinstance(loaded_stats, dict):
                    logger.warning(
                        "모델 메타 형식이 dict가 아닙니다: %s — 예측을 차단합니다.",
                        type(loaded_stats).__name__,
                    )
                elif not any(k in loaded_stats for k in ("hist", "store", "item")):
                    logger.warning(
                        "모델 메타 구조가 예상과 다릅니다(keys=%s) — 예측을 차단합니다.",
                        sorted(loaded_stats.keys()),
                    )
                else:
                    self.stats = loaded_stats
                    self.meta_loaded = True
            except Exception as exc:
                logger.warning("모델 메타 로드 실패 — 예측을 차단합니다: %s", exc)
        else:
            logger.warning("모델 메타 파일이 없습니다: %s — 예측을 차단합니다.", self.meta_path)

        logger.info(
            "예측 모델 로드 완료 (model=%s, meta_keys=%s)",
            type(self.model).__name__,
            sorted(self.stats.keys()),
        )
        return True

    def evaluate(self, test_df: pd.DataFrame) -> Dict[str, float]:
        """다양한 지표를 활용한 모델 성능 평가 (MAE, MAPE, RMSE, R2)"""
        if self.model is None:
            return {}
        X_test, y_test = self._prepare_training_data(test_df, is_training=False)
        if X_test.empty:
            return {}

        preds = self.model.predict(X_test)
        y_test_vals = y_test.values

        mae = np.mean(np.abs(preds - y_test_vals))
        rmse = np.sqrt(np.mean((preds - y_test_vals) ** 2))

        mask = y_test_vals > 0
        mape = np.mean(np.abs((y_test_vals[mask] - preds[mask]) / y_test_vals[mask])) * 100 if np.any(mask) else 0.0

        ss_res = np.sum((y_test_vals - preds) ** 2)
        ss_tot = np.sum((y_test_vals - np.mean(y_test_vals)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

        return {"MAE": float(mae), "MAPE": float(mape), "RMSE": float(rmse), "R2": float(r2)}

    def predict_next_hour_sales(
        self,
        store_cd: str,
        item_cd: str,
        current_time: datetime,
        history_df: pd.DataFrame,
        campaign_df: Optional[pd.DataFrame] = None,
    ) -> float:
        """1시간 후 판매 수량 예측 (ML 예측값과 실시간 판매 속도 하이브리드 보정)"""
        if self.model is None:
            return 0.0
        if not self.meta_loaded:
            raise RuntimeError(
                "모델 메타(model_meta.joblib)가 로드되지 않았습니다. "
                "python scripts/train.py를 먼저 실행해 모델을 생성하세요."
            )

        target_time = current_time + timedelta(hours=1)
        target_hour = target_time.hour
        weekday = target_time.weekday()
        target_date_str = target_time.strftime('%Y%m%d')

        # 4주 과거 중앙값 기준 조회 (요일·시간대 일치 행)
        hist_df = self.stats.get('hist', pd.DataFrame())
        hist_4w_median = 0.0
        if not hist_df.empty:
            match = hist_df[
                (hist_df['MASKED_STOR_CD'] == store_cd) &
                (hist_df['ITEM_CD'] == item_cd) &
                (hist_df['weekday'] == weekday) &
                (hist_df['hour'] == target_hour)
            ]
            if not match.empty:
                hist_4w_median = float(match['hist_4w_avg'].iloc[0])

        # 캠페인·프로모션 적용 시 판매 증가 승수 산정
        promo_multiplier = 1.0
        if campaign_df is not None and not campaign_df.empty:
            active_promos = campaign_df[
                (campaign_df['item_cd'] == item_cd) &
                (campaign_df['start_dt'] <= target_date_str) &
                (campaign_df['fnsh_dt'] >= target_date_str)
            ]
            if not active_promos.empty:
                max_dc_rate = pd.to_numeric(active_promos['dc_rate_amt'], errors='coerce').max()
                if pd.notna(max_dc_rate) and max_dc_rate > 0:
                    promo_multiplier = 1.0 + (max_dc_rate / 100) * 1.5 if max_dc_rate <= 100 else 1.3
                else:
                    promo_multiplier = 1.4

        # 최근 3시간 실시간 판매 속도 추출 (lag 피처 계산용)
        recent_cutoff = current_time - timedelta(hours=3)
        if history_df.empty or 'MASKED_STOR_CD' not in history_df.columns:
            df_recent = pd.DataFrame()
        else:
            df_recent = history_df[
                (history_df['MASKED_STOR_CD'] == store_cd) &
                (history_df['ITEM_CD'] == item_cd)
            ].copy()

        if not df_recent.empty:
            df_recent['sale_datetime'] = (
                pd.to_datetime(df_recent['SALE_DT'], format='%Y%m%d', errors='coerce')
                + pd.to_timedelta(pd.to_numeric(df_recent['TMZON_DIV'], errors='coerce').fillna(0).astype(int), unit='h')
            )
            recent = df_recent[
                (df_recent['sale_datetime'] > recent_cutoff) &
                (df_recent['sale_datetime'] <= current_time)
            ].sort_values('sale_datetime')
        else:
            recent = pd.DataFrame()

        lag_1h = recent['SALE_QTY'].iloc[-1] if len(recent) >= 1 else 0
        lag_2h = recent['SALE_QTY'].iloc[-2] if len(recent) >= 2 else 0
        current_median_velocity = recent['SALE_QTY'].median() if not recent.empty else 0

        X_pred = pd.DataFrame([[
            target_hour, weekday, 1 if weekday >= 5 else 0,
            lag_1h, lag_2h, current_median_velocity,
            self.stats.get('store', {}).get(store_cd, 0),
            self.stats.get('item', {}).get(item_cd, 0),
            hist_4w_median
        ]], columns=self.feature_cols)

        ml_pred = float(self.model.predict(X_pred)[0]) if self.model else 0.0

        # 예측 혼합: 과거 이력 비중 높게 유지, 실시간 속도로 소폭 보정
        # 과거 데이터 충분(>5)이면 안정 우선(0.95/0.05), 부족하면 유연(0.8/0.2)
        if hist_4w_median > 5:
            base_pred = (hist_4w_median * 0.95) + (ml_pred * 0.05)
            trend_ratio = np.clip(current_median_velocity / hist_4w_median if hist_4w_median > 0 else 1.0, 0.95, 1.05)
            final_pred = base_pred * trend_ratio
        else:
            base_pred = (hist_4w_median * 0.8) + (ml_pred * 0.2)
            trend_ratio = np.clip(current_median_velocity / hist_4w_median if hist_4w_median > 0 else 1.0, 0.8, 1.2)
            final_pred = base_pred * trend_ratio

        # 프로모션 승수 적용 및 과거 범위 기반 클리핑
        if promo_multiplier > 1.0:
            final_pred = final_pred * promo_multiplier
            if hist_4w_median > 0:
                final_pred = np.clip(final_pred, hist_4w_median * 0.7, hist_4w_median * promo_multiplier * 1.5)
        else:
            if hist_4w_median > 0:
                final_pred = np.clip(final_pred, hist_4w_median * 0.7, hist_4w_median * 1.3)

        return float(max(0, round(final_pred, 0)))
