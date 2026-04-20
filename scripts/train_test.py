import os
import sys
import logging
import pandas as pd
import numpy as np
import joblib

# ML Models
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
import lightgbm as lgb

from sqlalchemy import create_engine

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multi_model_scenarios")

def chance_loss_objective_fixed(y_true, y_pred):
    penalty = 5.0
    residual = y_pred - y_true
    grad = np.where(residual < 0, penalty * residual, residual)
    hess = np.where(residual < 0, penalty, 1.0)
    return grad, hess

class MultiModelScenarioTrainer:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        self.model_dir = os.path.join(ai_dir, 'models')
        if not os.path.exists(self.model_dir):
            os.makedirs(self.model_dir)
        
    def load_mart_data(self) -> pd.DataFrame:
        logger.info("Data Mart 테이블 로딩 시작 (테스트를 위해 상위 10개 지점 필터링)...")
        # 테스트 속도를 위해 상위 10개 지점만 추출
        query = """
        SELECT * FROM ai_sales_data_mart 
        WHERE masked_stor_cd IN ('POC_999', 'POC_029', 'POC_019', 'POC_006', 'POC_012', 'POC_025', 'POC_020', 'POC_007', 'POC_011', 'POC_009')
        """
        df = pd.read_sql(query, self.engine)
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        logger.info(f"데이터 로드 완료: {len(df)}건")
        return df

    def run_scenarios(self, df: pd.DataFrame):
        test_end = pd.to_datetime('2026-03-10 23:59:59')
        test_start = test_end - pd.Timedelta(days=30)
        ly_start, ly_end = pd.to_datetime('2025-03-11 00:00:00'), pd.to_datetime('2025-03-11 00:00:00') + pd.Timedelta(days=30)
        prev_end, prev_start = test_start, test_start - pd.Timedelta(days=30)
        full_train_start = pd.to_datetime('2025-03-11 00:00:00')

        test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
        y_test_actual = test_df['TARGET_1H_AHEAD'].values
        scenarios = {
            "1. 전년 동월": df[(df['DATETIME'] > ly_start) & (df['DATETIME'] <= ly_end)].copy(), 
            "2. 직전월": df[(df['DATETIME'] > prev_start) & (df['DATETIME'] <= prev_end)].copy(), 
            "3. 전체 기간": df[(df['DATETIME'] >= full_train_start) & (df['DATETIME'] <= test_start)].copy()
        }

        eval_results = []
        tscv = TimeSeriesSplit(n_splits=3)

        for scenario_name, train_data in scenarios.items():
            if train_data.empty: continue
            s_idx = scenario_name.split(".")[0]
            f_scaler, t_scaler = StandardScaler(), StandardScaler()
            X_tr, y_tr = train_data[self.features].copy(), train_data[['TARGET_1H_AHEAD']].copy()
            X_te, y_te = test_df[self.features].copy(), test_df[['TARGET_1H_AHEAD']].copy()
            for c in self.features: X_tr[c], X_te[c] = X_tr[c].astype(float), X_te[c].astype(float)
            X_tr.loc[:, self.features] = f_scaler.fit_transform(X_tr)
            X_te.loc[:, self.features] = f_scaler.transform(X_te)
            y_tr_scaled = t_scaler.fit_transform(y_tr).flatten()
            
            joblib.dump(f_scaler, os.path.join(self.model_dir, f'f_scaler_s{s_idx}.joblib'))
            joblib.dump(t_scaler, os.path.join(self.model_dir, f't_scaler_s{s_idx}.joblib'))

            logger.info(f"[{scenario_name}] LightGBM 학습 중...")
            lgb_reg = lgb.LGBMRegressor(random_state=42, verbosity=-1, objective=chance_loss_objective_fixed)
            lgb_search = RandomizedSearchCV(lgb_reg, {'learning_rate':[0.05, 0.1], 'max_depth':[5, 7], 'n_estimators':[100, 200]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1)
            lgb_search.fit(X_tr, y_tr_scaled)
            
            preds_actual = t_scaler.inverse_transform(lgb_search.predict(X_te).reshape(-1, 1)).flatten()
            eval_results.append({'Model': 'LightGBM', 'Scenario': scenario_name, 'RMSE': np.sqrt(mean_squared_error(y_test_actual, preds_actual)), 'MAE': mean_absolute_error(y_test_actual, preds_actual)})

        print("\n" + "="*80)
        print(f"{'다중 모델 & 기간 조합 성능 비교 (Data Mart 사용)':^80}")
        print("-" * 80)
        for r in eval_results:
            print(f"{r['Model']:<15} | {r['Scenario']:<20} | {r['RMSE']:<8.4f} | {r['MAE']:<8.4f}")
        print("="*80)

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = MultiModelScenarioTrainer(db_url)
    df = trainer.load_mart_data()
    if not df.empty: trainer.run_scenarios(df)

if __name__ == "__main__":
    run_pipeline()
