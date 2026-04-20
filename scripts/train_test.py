import os
import sys
import logging
import pandas as pd
import numpy as np
import joblib
import time

# ML Models
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.ensemble import RandomForestRegressor
import lightgbm as lgb
try:
    import xgboost as xgb
except ImportError:
    xgb = None

try:
    from catboost import CatBoostRegressor
except ImportError:
    CatBoostRegressor = None

from sqlalchemy import create_engine

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multi_model_scenarios_all_stores")

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
        logger.info("Data Mart 테이블 로딩 시작 (전체 매장 대상)...")
        # 모든 지점 조회를 위해 필터 제거
        query = 'SELECT * FROM ai_sales_data_mart'
        df = pd.read_sql(query, self.engine)
        
        # 컬럼명 대문자 통일 및 전처리
        df.columns = [c.upper() for c in df.columns]
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        logger.info(f"전체 데이터 로드 완료: {len(df):,}건 (매장 수: {df['MASKED_STOR_CD'].nunique()}개)")
        return df

    def run_scenarios(self, df: pd.DataFrame):
        test_end = pd.to_datetime('2026-03-10 23:59:59')
        test_start = test_end - pd.Timedelta(days=30)
        ly_start, ly_end = pd.to_datetime('2025-03-11 00:00:00'), pd.to_datetime('2025-03-11 00:00:00') + pd.Timedelta(days=30)
        prev_end, prev_start = test_start, test_start - pd.Timedelta(days=30)
        full_train_start = pd.to_datetime('2025-03-11 00:00:00')

        test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
        if test_df.empty:
            logger.error("❌ 평가 기간 데이터가 없습니다.")
            return

        y_test_actual = test_df['TARGET_1H_AHEAD'].values
        scenarios = {
            "1. 전년 동월": df[(df['DATETIME'] > ly_start) & (df['DATETIME'] <= ly_end)].copy(), 
            "2. 직전월": df[(df['DATETIME'] > prev_start) & (df['DATETIME'] <= prev_end)].copy(), 
            "3. 전체 기간": df[(df['DATETIME'] >= full_train_start) & (df['DATETIME'] <= test_start)].copy()
        }

        eval_results = []
        tscv = TimeSeriesSplit(n_splits=2) # 전체 매장 데이터이므로 효율성을 위해 split 수 조정

        for scenario_name, train_data in scenarios.items():
            if train_data.empty: continue
            
            logger.info(f"\n🚀 [{scenario_name}] 학습 시나리오 시작 (데이터 건수: {len(train_data):,}건)...")
            f_scaler, t_scaler = StandardScaler(), StandardScaler()
            X_tr, y_tr = train_data[self.features].astype(float), train_data[['TARGET_1H_AHEAD']].astype(float)
            X_te, y_te = test_df[self.features].astype(float), test_df[['TARGET_1H_AHEAD']].astype(float)
            
            # [Fix] 스케일링 후 데이터프레임으로 복원하여 피처 이름 유지 (경고 방지)
            X_tr_sc = pd.DataFrame(f_scaler.fit_transform(X_tr), columns=self.features)
            X_te_sc = pd.DataFrame(f_scaler.transform(X_te), columns=self.features)
            y_tr_sc = t_scaler.fit_transform(y_tr).flatten()

            # 1. LightGBM
            start_t = time.time()
            lgb_reg = lgb.LGBMRegressor(random_state=42, verbosity=-1, objective=chance_loss_objective_fixed)
            lgb_search = RandomizedSearchCV(lgb_reg, {'learning_rate':[0.05, 0.1], 'max_depth':[5, 7]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
            lgb_search.fit(X_tr_sc, y_tr_sc)
            preds = t_scaler.inverse_transform(lgb_search.predict(X_te_sc).reshape(-1, 1)).flatten()
            preds = np.maximum(0, preds) # 음수 예측 방지
            eval_results.append({'Scenario': scenario_name, 'Model': 'LightGBM', 'RMSE': np.sqrt(mean_squared_error(y_test_actual, preds)), 'MAE': mean_absolute_error(y_test_actual, preds), 'Time': time.time()-start_t})

            # 2. XGBoost
            if xgb is not None:
                start_t = time.time()
                xgb_reg = xgb.XGBRegressor(random_state=42, verbosity=0)
                xgb_search = RandomizedSearchCV(xgb_reg, {'learning_rate':[0.05, 0.1], 'max_depth':[5, 7]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
                xgb_search.fit(X_tr_sc, y_tr_sc)
                preds = t_scaler.inverse_transform(xgb_search.predict(X_te_sc).reshape(-1, 1)).flatten()
                preds = np.maximum(0, preds)
                eval_results.append({'Scenario': scenario_name, 'Model': 'XGBoost', 'RMSE': np.sqrt(mean_squared_error(y_test_actual, preds)), 'MAE': mean_absolute_error(y_test_actual, preds), 'Time': time.time()-start_t})

            # 3. CatBoost
            if CatBoostRegressor is not None:
                start_t = time.time()
                cat_reg = CatBoostRegressor(random_seed=42, verbose=0, allow_writing_files=False)
                cat_search = RandomizedSearchCV(cat_reg, {'learning_rate':[0.05, 0.1], 'depth':[4, 6]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
                cat_search.fit(X_tr_sc, y_tr_sc)
                preds = t_scaler.inverse_transform(cat_search.predict(X_te_sc).reshape(-1, 1)).flatten()
                preds = np.maximum(0, preds)
                eval_results.append({'Scenario': scenario_name, 'Model': 'CatBoost', 'RMSE': np.sqrt(mean_squared_error(y_test_actual, preds)), 'MAE': mean_absolute_error(y_test_actual, preds), 'Time': time.time()-start_t})

            # 4. RandomForest
            start_t = time.time()
            rf_reg = RandomForestRegressor(random_state=42, n_jobs=-1)
            rf_search = RandomizedSearchCV(rf_reg, {'max_depth':[10], 'n_estimators':[50]}, n_iter=1, cv=tscv, scoring='neg_mean_absolute_error', random_state=42)
            rf_search.fit(X_tr_sc, y_tr_sc)
            preds = t_scaler.inverse_transform(rf_search.predict(X_te_sc).reshape(-1, 1)).flatten()
            preds = np.maximum(0, preds)
            eval_results.append({'Scenario': scenario_name, 'Model': 'RandomForest', 'RMSE': np.sqrt(mean_squared_error(y_test_actual, preds)), 'MAE': mean_absolute_error(y_test_actual, preds), 'Time': time.time()-start_t})

        print("\n" + "="*100)
        print(f"{'🏆 전체 매장 대상 다중 알고리즘 시나리오 결과 리포트':^100}")
        print("="*100)
        res_df = pd.DataFrame(eval_results).sort_values(by=['Scenario', 'MAE'])
        for _, row in res_df.iterrows():
            print(f"{row['Scenario']:<15} | {row['Model']:<15} | RMSE: {row['RMSE']:<8.4f} | MAE: {row['MAE']:<8.4f} | 소요시간: {row['Time']:.1f}초")
        print("="*100)

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = MultiModelScenarioTrainer(db_url)
    df = trainer.load_mart_data()
    if not df.empty: trainer.run_scenarios(df)

if __name__ == "__main__":
    run_pipeline()
