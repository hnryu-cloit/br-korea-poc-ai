import os
import sys
import logging
import pandas as pd
import numpy as np

# ML Models
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
import lightgbm as lgb

try:
    from catboost import CatBoostRegressor
except ImportError:
    pass

from sqlalchemy import create_engine

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("time_series_backtesting")

def chance_loss_objective(y_true, y_pred, penalty=5.0):
    residual = y_pred - y_true
    grad = np.where(residual < 0, penalty * residual, residual)
    hess = np.where(residual < 0, penalty, 1.0)
    return grad, hess

def get_lgb_objective(penalty=5.0):
    return lambda y_true, y_pred: chance_loss_objective(y_true, y_pred, penalty=penalty)

class ExpandingWindowBacktester:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        
    def load_mart_data(self) -> pd.DataFrame:
        logger.info("Data Mart 테이블 로딩 시작 (상위 10개 지점 필터링)...")
        # 교차 검증 속도를 위해 상위 지점만 가져오기
        query = """
        SELECT * FROM ai_sales_data_mart 
        WHERE masked_stor_cd IN ('POC_999', 'POC_029', 'POC_019', 'POC_006', 'POC_012', 'POC_025', 'POC_020', 'POC_007', 'POC_011', 'POC_009')
        """
        df = pd.read_sql(query, self.engine)
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        logger.info(f"데이터 로드 완료: {len(df)}건")
        return df

    def run_backtesting(self, df: pd.DataFrame, num_folds=6):
        base_test_end = pd.to_datetime('2026-03-10 23:59:59')
        full_train_start = pd.to_datetime('2025-03-11 00:00:00')
        all_results = []
        penalty_val = 5.0
        
        for fold in range(num_folds):
            test_end = base_test_end - pd.Timedelta(days=30 * fold)
            test_start = test_end - pd.Timedelta(days=30)
            train_period_str = f"{full_train_start.date()}~{test_start.date()}"
            test_period_str = f"{test_start.date()}~{test_end.date()}"
            
            logger.info(f"\n[Fold {fold+1}/{num_folds}] {test_period_str}")
            train_df = df[(df['DATETIME'] >= full_train_start) & (df['DATETIME'] <= test_start)].copy()
            test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
            if len(train_df) < 10000 or len(test_df) < 1000: continue

            y_test_actual = test_df['TARGET_1H_AHEAD'].values
            f_scaler, t_scaler = StandardScaler(), StandardScaler()
            X_tr, y_tr = train_df[self.features].copy(), train_df[['TARGET_1H_AHEAD']].copy()
            X_te, y_te = test_df[self.features].copy(), test_df[['TARGET_1H_AHEAD']].copy()
            for c in self.features: X_tr[c], X_te[c] = X_tr[c].astype(float), X_te[c].astype(float)
            X_tr.loc[:, self.features] = f_scaler.fit_transform(X_tr)
            X_te.loc[:, self.features] = f_scaler.transform(X_te)
            y_tr_scaled = t_scaler.fit_transform(y_tr).flatten()
            
            tscv = TimeSeriesSplit(n_splits=3)
            
            def evaluate_and_store(model_name, preds_scaled):
                preds_actual = t_scaler.inverse_transform(preds_scaled.reshape(-1, 1)).flatten()
                rmse = np.sqrt(mean_squared_error(y_test_actual, preds_actual))
                mae = mean_absolute_error(y_test_actual, preds_actual)
                est_waste = np.sum(np.maximum(0, preds_actual - y_test_actual))
                est_chance_loss = np.sum(np.maximum(0, y_test_actual - preds_actual))
                accuracy_pct = max(0, (1 - (mae / np.mean(y_test_actual))) * 100) if np.mean(y_test_actual) > 0 else 0
                all_results.append({'Fold': fold+1, 'Model': model_name, 'RMSE': rmse, 'MAE': mae, 'Waste': est_waste, 'ChanceLoss': est_chance_loss, 'Accuracy(%)': accuracy_pct})

            # --- LightGBM ---
            lgb_reg = lgb.LGBMRegressor(random_state=42, verbosity=-1, objective=get_lgb_objective(penalty=penalty_val))
            lgb_search = RandomizedSearchCV(lgb_reg, {'learning_rate':[0.05, 0.1], 'max_depth':[5, 7], 'n_estimators':[100, 200]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1)
            lgb_search.fit(X_tr, y_tr_scaled)
            evaluate_and_store("LightGBM", lgb_search.predict(X_te))

            # --- CatBoost ---
            try:
                from catboost import CatBoostRegressor
                cat_reg = CatBoostRegressor(random_seed=42, verbose=0, loss_function='MAE', allow_writing_files=False)
                cat_search = RandomizedSearchCV(cat_reg, {'learning_rate':[0.05, 0.1], 'depth':[4, 6], 'iterations':[100]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1)
                cat_search.fit(X_tr, y_tr_scaled)
                evaluate_and_store("CatBoost", cat_search.predict(X_te))
            except ImportError:
                pass

        res_df = pd.DataFrame(all_results)
        avg_df = res_df.groupby('Model')[['RMSE', 'MAE', 'Waste', 'ChanceLoss', 'Accuracy(%)']].mean().reset_index()
        print("\n" + "="*110)
        print(f"{'🏆 모델별 6개월 평균 성능 지표 (Data Mart 사용)':^110}")
        print("-" * 110)
        for _, row in avg_df.sort_values(by='ChanceLoss').iterrows():
            print(f"{row['Model']:<15} | {row['RMSE']:<12.4f} | {row['Waste']:<15.1f} | {row['ChanceLoss']:<15.1f} | {row['Accuracy(%)']:.2f}%")
        print("="*110)

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    tester = ExpandingWindowBacktester(db_url)
    df = tester.load_mart_data()
    if not df.empty: tester.run_backtesting(df, num_folds=6)

if __name__ == "__main__":
    run_pipeline()
