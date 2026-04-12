from __future__ import annotations

import datetime
from typing import Any, List, Tuple, Dict, Optional
import numpy as np
import pandas as pd
from common.logger import init_logger
import joblib
import os

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    from sklearn.ensemble import RandomForestRegressor
    HAS_LGB = False

logger = init_logger("predictor")

class InventoryPredictor:
    """
    [Balanced High-Precision] 안정성과 정확도를 모두 잡은 최종 예측 엔진
    - GBDT 기반 안정적 학습 및 변동성 보정 로직 적용
    """

    def __init__(self, model_dir: Optional[str] = None):
        self.model = None
        if model_dir is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.model_dir = os.path.join(os.path.dirname(current_dir), "models")
        else:
            self.model_dir = model_dir

        self.model_path = os.path.join(self.model_dir, "inventory_lgbm_model.pkl")
        self.meta_path = os.path.join(self.model_dir, "model_meta.joblib")

        # 핵심 피처 정예화 (복잡도 감소, 정보밀도 상승)
        self.feature_cols = [
            'hour', 'weekday', 'is_weekend',
            'lag_1h', 'lag_2h', 'rolling_mean_3h',
            'store_avg', 'item_avg'
        ]

        self.stats = {}
        self.load_model()

    def _prepare_training_data(self, history_df: pd.DataFrame, is_training: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        df = history_df.copy()

        # [데이터 밸런싱] 판매가 0인 데이터가 너무 많아 모델이 보수적으로 학습됨
        if is_training:
            zero_sales = df[df['SALE_QTY'] == 0]
            non_zero_sales = df[df['SALE_QTY'] > 0]

            # 판매 0인 데이터를 판매가 있는 데이터의 1.5배 수준으로만 샘플링 (나머지 버림)
            sample_size = min(len(zero_sales), int(len(non_zero_sales) * 1.5))
            zero_sales_sampled = zero_sales.sample(n=sample_size, random_state=42)

            df = pd.concat([non_zero_sales, zero_sales_sampled]).sort_values(['SALE_DT', 'TMZON_DIV'])

        df['sale_dt_dt'] = pd.to_datetime(df['SALE_DT'], format='%Y%m%d')
        df['hour'] = df['TMZON_DIV'].astype(int)
        df['weekday'] = df['sale_dt_dt'].dt.weekday
        df['is_weekend'] = (df['weekday'] >= 5).astype(int)

        df = df.sort_values(['MASKED_STOR_CD', 'ITEM_CD', 'SALE_DT', 'hour'])
        group = df.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY']

        df['lag_1h'] = group.shift(1).fillna(0)
        df['lag_2h'] = group.shift(2).fillna(0)
        df['rolling_mean_3h'] = group.transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean()).fillna(0)

        if is_training:
            # 이상치 제거
            q_limit = df['SALE_QTY'].quantile(0.99)
            df = df[df['SALE_QTY'] <= q_limit]

            self.stats['store'] = df.groupby('MASKED_STOR_CD')['SALE_QTY'].mean().to_dict()
            self.stats['item'] = df.groupby('ITEM_CD')['SALE_QTY'].mean().to_dict()
            
        df['store_avg'] = df['MASKED_STOR_CD'].map(self.stats.get('store', {})).fillna(0)
        df['item_avg'] = df['ITEM_CD'].map(self.stats.get('item', {})).fillna(0)

        return df[self.feature_cols], df['SALE_QTY']

    def train(self, history_df: pd.DataFrame):
        """
        데이터 밸런싱 및 고밀도 학습
        """
        X, y = self._prepare_training_data(history_df, is_training=True)

        if HAS_LGB:
            # 판매량이 높은 구간의 학습 중요도를 높이기 위한 샘플 가중치 계산
            sample_weight = np.log1p(y) + 1.0 # 많이 팔릴수록 가중치 상향

            params = {
                'objective': 'regression',
                'metric': 'mae',
                'verbosity': -1,
                'boosting_type': 'gbdt',
                'learning_rate': 0.05,
                'num_leaves': 63,
                'max_depth': -1,
                'min_child_samples': 10,
                'feature_fraction': 0.8,
                'lambda_l1': 0.05,
                'n_jobs': -1
            }
            train_data = lgb.Dataset(X, label=y, weight=sample_weight)
            self.model = lgb.train(params, train_data, num_boost_round=500)
        else:
            self.model = RandomForestRegressor(n_estimators=100)
            self.model.fit(X, y)

        self.save_model()
        logger.info("데이터 밸런싱이 적용된 최적화 모델 재학습 완료.")

    def save_model(self):
        if not os.path.exists(self.model_dir): os.makedirs(self.model_dir)
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.stats, self.meta_path)

    def load_model(self) -> bool:
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                if os.path.exists(self.meta_path):
                    self.stats = joblib.load(self.meta_path)
                return True
            except: pass
        return False

    def predict_next_hour_sales(self, store_cd: str, item_cd: str, current_time: datetime, history_df: pd.DataFrame) -> float:
        """
        [Hybrid Inference] ML 예측 + 실시간 판매 속도 보정 (정확도 80% 목표)
        """
        if self.model is None: return 3.0
            
        target_time = current_time + datetime.timedelta(hours=1)
        weekday = target_time.weekday()

        # 1. 최근 3시간 판매 속도 계산
        recent = history_df[
            (history_df['MASKED_STOR_CD'] == store_cd) &
            (history_df['ITEM_CD'] == item_cd)
        ].sort_values(['SALE_DT', 'TMZON_DIV']).tail(3)

        lag_1h = recent['SALE_QTY'].iloc[-1] if len(recent) >= 1 else 0
        lag_2h = recent['SALE_QTY'].iloc[-2] if len(recent) >= 2 else 0
        current_velocity = recent['SALE_QTY'].mean() if not recent.empty else 0

        # 2. ML 모델 원시 예측
        X_pred = pd.DataFrame([[
            target_time.hour, weekday, 1 if weekday >= 5 else 0,
            lag_1h, lag_2h, current_velocity,
            self.stats.get('store', {}).get(store_cd, 0),
            self.stats.get('item', {}).get(item_cd, 0)
        ]], columns=self.feature_cols)

        ml_pred = float(self.model.predict(X_pred)[0])

        # 3. 하이브리드 보정 (앙상블 효과)
        # ML 예측값과 최근 판매 속도를 7:3 비율로 섞어 갑작스러운 수요 변화에 대응
        final_pred = (ml_pred * 0.7) + (current_velocity * 0.3)

        # 4. 베이스라인 보정 (계절성 반영)
        # 만약 모델이 너무 낮게 예측할 경우 최소 평균치 보전
        item_avg = self.stats.get('item', {}).get(item_cd, 1.0)
        final_pred = max(final_pred, item_avg * 0.5)

        return max(0.0, round(final_pred, 2))

    def predict_with_confidence(
        self,
        store_cd: str,
        item_cd: str,
        current_time: datetime,
        history_df: pd.DataFrame,
    ) -> Dict[str, Any]:
        """
        예측값과 신뢰구간을 함께 반환한다.
        - confidence_level: 최근 데이터 충분도 기반 0~1 값
        - 신뢰구간: 과거 잔차(residual) 표준편차 기반 ±1σ 구간 (데이터 부족 시 비율 기반 fallback)
        """
        predicted = self.predict_next_hour_sales(store_cd, item_cd, current_time, history_df)

        recent = history_df[
            (history_df['MASKED_STOR_CD'] == store_cd)
            & (history_df['ITEM_CD'] == item_cd)
        ]
        data_points = len(recent)
        confidence_level = min(1.0, data_points / 50)  # 50행 이상이면 신뢰도 1.0

        # 과거 잔차 기반 표준편차 계산 (데이터 10개 이상일 때)
        std_dev = None
        if self.model is not None and data_points >= 10:
            try:
                X_hist, y_hist = self._prepare_training_data(recent.copy(), is_training=False)
                if len(X_hist) >= 5:
                    y_hat = self.model.predict(X_hist)
                    residuals = np.array(y_hist) - y_hat
                    std_dev = float(np.std(residuals))
            except Exception:
                std_dev = None

        if std_dev is not None and std_dev > 0:
            # ±1σ 구간 (약 68% 신뢰구간)
            lower_bound = round(max(0.0, predicted - std_dev), 2)
            upper_bound = round(predicted + std_dev, 2)
        else:
            # Fallback: 데이터 부족 시 비율 기반 구간
            spread = 0.2 + (1.0 - confidence_level) * 0.2
            lower_bound = round(max(0.0, predicted * (1 - spread)), 2)
            upper_bound = round(predicted * (1 + spread), 2)

        logger.info(
            "predict_with_confidence: store=%s, item=%s, pred=%.2f [%.2f~%.2f] confidence=%.2f std=%.3f",
            store_cd, item_cd, predicted, lower_bound, upper_bound, confidence_level,
            std_dev if std_dev is not None else -1.0,
        )

        return {
            "predicted": predicted,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "confidence_level": round(confidence_level, 2),
        }

    def evaluate(self, test_df: pd.DataFrame) -> Dict[str, float]:
        if self.model is None: return {"error": "미학습"}
        X_test, y_test = self._prepare_training_data(test_df, is_training=False)
        y_pred = self.model.predict(X_test)
        return {"MAE": float(np.mean(np.abs(y_test - y_pred))), "RMSE": float(np.sqrt(np.mean((y_test - y_pred)**2)))}
