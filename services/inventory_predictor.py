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
except ImportError:
    from sklearn.ensemble import RandomForestRegressor
    HAS_LGB = False

logger = init_logger("inventory_predictor")


class InventoryPredictor:
    """[Balanced High-Precision] 안정성과 정확도를 모두 잡은 최종 예측 엔진"""

    def __init__(self, model_dir: Optional[str] = None):
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
        self.load_model()

    def _prepare_training_data(self, history_df: pd.DataFrame, is_training: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        df = history_df.copy()

        df['SALE_QTY'] = pd.to_numeric(df['SALE_QTY'], errors='coerce').fillna(0)
        df['TMZON_DIV'] = pd.to_numeric(df['TMZON_DIV'], errors='coerce').fillna(0).astype(int)

        if is_training:
            zero_sales = df[df['SALE_QTY'] == 0]
            non_zero_sales = df[df['SALE_QTY'] > 0]
            sample_size = min(len(zero_sales), int(len(non_zero_sales) * 1.5))
            zero_sales_sampled = zero_sales.sample(n=sample_size, random_state=42) if not zero_sales.empty else zero_sales
            df = pd.concat([non_zero_sales, zero_sales_sampled]).sort_values(['SALE_DT', 'TMZON_DIV'])

        df['sale_dt_dt'] = pd.to_datetime(df['SALE_DT'], format='%Y%m%d')
        df['hour'] = df['TMZON_DIV']
        df['weekday'] = df['sale_dt_dt'].dt.weekday
        df['is_weekend'] = (df['weekday'] >= 5).astype(int)

        df = df.sort_values(['MASKED_STOR_CD', 'ITEM_CD', 'SALE_DT', 'hour'])

        hist_stats = df.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'weekday', 'hour'])['SALE_QTY'].median().reset_index()
        hist_stats.rename(columns={'SALE_QTY': 'hist_4w_avg'}, inplace=True)

        if is_training:
            self.stats['hist'] = hist_stats
            self.stats['store'] = df.groupby('MASKED_STOR_CD')['SALE_QTY'].median().to_dict()
            self.stats['item'] = df.groupby('ITEM_CD')['SALE_QTY'].median().to_dict()

        df = pd.merge(df, hist_stats, on=['MASKED_STOR_CD', 'ITEM_CD', 'weekday', 'hour'], how='left')

        group = df.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY']
        df['lag_1h'] = group.shift(1).fillna(0)
        df['lag_2h'] = group.shift(2).fillna(0)
        df['rolling_mean_3h'] = group.transform(lambda x: x.shift(1).rolling(3, min_periods=1).median()).fillna(0)

        df['store_avg'] = df['MASKED_STOR_CD'].map(self.stats.get('store', {})).fillna(0)
        df['item_avg'] = df['ITEM_CD'].map(self.stats.get('item', {})).fillna(0)

        if is_training:
            q_limit = df['SALE_QTY'].quantile(0.95)
            df = df[df['SALE_QTY'] <= q_limit]

        return df[self.feature_cols], df['SALE_QTY']

    def train(self, history_df: pd.DataFrame):
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
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                if os.path.exists(self.meta_path):
                    self.stats = joblib.load(self.meta_path)
                return True
            except:
                pass
        return False

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
        if self.model is None:
            return 0.0

        target_time = current_time + timedelta(hours=1)
        target_hour = target_time.hour
        weekday = target_time.weekday()
        target_date_str = target_time.strftime('%Y%m%d')

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

        recent_cutoff = current_time - timedelta(hours=3)
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

        if hist_4w_median > 5:
            base_pred = (hist_4w_median * 0.95) + (ml_pred * 0.05)
            trend_ratio = np.clip(current_median_velocity / hist_4w_median if hist_4w_median > 0 else 1.0, 0.95, 1.05)
            final_pred = base_pred * trend_ratio
        else:
            base_pred = (hist_4w_median * 0.8) + (ml_pred * 0.2)
            trend_ratio = np.clip(current_median_velocity / hist_4w_median if hist_4w_median > 0 else 1.0, 0.8, 1.2)
            final_pred = base_pred * trend_ratio

        if promo_multiplier > 1.0:
            final_pred = final_pred * promo_multiplier
            if hist_4w_median > 0:
                final_pred = np.clip(final_pred, hist_4w_median * 0.7, hist_4w_median * promo_multiplier * 1.5)
        else:
            if hist_4w_median > 0:
                final_pred = np.clip(final_pred, hist_4w_median * 0.7, hist_4w_median * 1.3)

        return float(max(0, round(final_pred, 0)))