import os
import sys
import logging
import pandas as pd
import numpy as np
from datetime import timedelta

# Scikit-learn & LightGBM
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
import lightgbm as lgb

# DB
from sqlalchemy import create_engine, text

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_pipeline_scaled")

# =====================================================================
# [커스텀 손실 함수] - Chance Loss 패널티
# =====================================================================
def chance_loss_objective(y_true, y_pred):
    residual = y_pred - y_true
    penalty = 5.0
    grad = np.where(residual < 0, penalty * residual, residual)
    hess = np.where(residual < 0, penalty, 1.0)
    return grad, hess

class HourlyInventoryTrainer:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        self.feature_scaler = StandardScaler()
        self.target_scaler = StandardScaler() # 정답(판매량) 수치를 맞추기 위한 스케일러
        
    def load_and_preprocess(self) -> pd.DataFrame:
        logger.info("1. 데이터 수집 및 시간(Hourly) 단위 전처리 시작...")
        
        sales_query = text('''
            SELECT 
                s.masked_stor_cd, s.item_cd, s.sale_dt, s.tmzon_div, s.sale_qty,
                CASE WHEN EXISTS (
                    SELECT 1 
                    FROM raw_campaign_master m 
                    JOIN raw_campaign_item i ON m.cmp_cd = i.cmp_cd
                    WHERE i.item_cd = s.item_cd 
                      AND s.sale_dt >= REPLACE(m.start_dt, '-', '') 
                      AND s.sale_dt <= REPLACE(m.fnsh_dt, '-', '')
                ) THEN 1 ELSE 0 END AS is_event
            FROM raw_daily_store_item_tmzon s
        ''')
        
        df_sales = pd.read_sql(sales_query, self.engine)
        
        df_sales.columns = [c.upper() for c in df_sales.columns]
        
        df_sales['DATETIME'] = pd.to_datetime(df_sales['SALE_DT'] + df_sales['TMZON_DIV'].astype(str).str.zfill(2), format='%Y%m%d%H')
        df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'DATETIME'])

        df_sales['HOUR'] = df_sales['DATETIME'].dt.hour
        df_sales['WEEKDAY'] = df_sales['DATETIME'].dt.weekday
        df_sales['IS_WEEKEND'] = (df_sales['WEEKDAY'] >= 5).astype(int)
        
        df_sales['HIST_4W_AVG'] = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['SALE_QTY'].transform(
            lambda x: x.rolling(window=4, min_periods=1).mean().shift(1).bfill()
        )
        
        upper_bound = df_sales['SALE_QTY'].quantile(0.99)
        df_sales.loc[(df_sales['IS_EVENT'] == 0) & (df_sales['SALE_QTY'] > upper_bound), 'SALE_QTY'] = upper_bound

        df_sales['TARGET_1H_AHEAD'] = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY'].shift(-1)
        df_sales.dropna(subset=['TARGET_1H_AHEAD'], inplace=True)

        return df_sales

    def run_scenarios(self, df: pd.DataFrame):
        test_end = pd.to_datetime('2026-03-10 23:59:59')
        test_start = test_end - pd.Timedelta(days=30)
        
        ly_end, ly_start = test_end - pd.DateOffset(years=1), test_start - pd.DateOffset(years=1)
        prev_end, prev_start = test_start, test_start - pd.Timedelta(days=30)
        full_train_start = pd.to_datetime('2025-03-11 00:00:00')

        test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
        train_ly = df[(df['DATETIME'] > ly_start) & (df['DATETIME'] <= ly_end)].copy()
        train_prev = df[(df['DATETIME'] > prev_start) & (df['DATETIME'] <= prev_end)].copy()
        train_full = df[(df['DATETIME'] >= full_train_start) & (df['DATETIME'] <= test_start)].copy()

        # [수치 맞추기] Scaler Fit & Transform
        if not train_full.empty:
            self.feature_scaler.fit(train_full[self.features])
            self.target_scaler.fit(train_full[['TARGET_1H_AHEAD']]) # Target 전용 스케일러

            for ds in [train_ly, train_prev, train_full, test_df]:
                if not ds.empty:
                    ds.loc[:, self.features] = self.feature_scaler.transform(ds[self.features])
                    ds.loc[:, ['TARGET_1H_AHEAD']] = self.target_scaler.transform(ds[['TARGET_1H_AHEAD']])

        X_test, y_test_scaled = test_df[self.features], test_df['TARGET_1H_AHEAD']
        # 실제 값(Unscaled)으로 복원해둔 정답지 (평가용)
        y_test_actual = self.target_scaler.inverse_transform(y_test_scaled.values.reshape(-1, 1)).flatten()

        results, models = {}, {}

        def train_and_tune(X_train, y_train, name):
            if X_train.empty: return None
            logger.info(f"==> {name} 학습 중 (Target Scaled)...")
            lgb_reg = lgb.LGBMRegressor(random_state=42, n_estimators=100)
            lgb_reg.set_params(**{'objective': chance_loss_objective})
            search = RandomizedSearchCV(lgb_reg, {'learning_rate': [0.01, 0.05, 0.1], 'max_depth': [5, 7], 'num_leaves': [20, 31]}, n_iter=5, cv=TimeSeriesSplit(n_splits=3), scoring='neg_mean_absolute_error', n_jobs=-1)
            search.fit(X_train, y_train)
            return search.best_estimator_

        def evaluate(preds_scaled, name):
            # [다시 풀어주는 과정] 예측값을 실제 판매량(단위: 개수)으로 복원
            preds_actual = self.target_scaler.inverse_transform(preds_scaled.reshape(-1, 1)).flatten()
            
            rmse = np.sqrt(mean_squared_error(y_test_actual, preds_actual))
            mae = mean_absolute_error(y_test_actual, preds_actual)
            chance_loss = np.sum(np.maximum(0, y_test_actual - preds_actual))
            results[name] = {'RMSE': rmse, 'MAE': mae, 'ChanceLoss': chance_loss}

        m1 = train_and_tune(train_ly[self.features], train_ly['TARGET_1H_AHEAD'], "1. 전년 동월")
        if m1: models['m1'], evaluate(m1.predict(X_test), "1. 전년 동월")

        m2 = train_and_tune(train_prev[self.features], train_prev['TARGET_1H_AHEAD'], "2. 직전월")
        if m2: models['m2'], evaluate(m2.predict(X_test), "2. 직전월")

        m4 = train_and_tune(train_full[self.features], train_full['TARGET_1H_AHEAD'], "4. 전체 기간")
        if m4: models['m4'], evaluate(m4.predict(X_test), "4. 전체 기간")

        if 'm1' in models and 'm2' in models:
            pred_ens_scaled = (models['m1'].predict(X_test) + models['m2'].predict(X_test)) / 2.0
            evaluate(pred_ens_scaled, "3. 앙상블 (M1+M2)")

        print("\n" + "="*75)
        print(f"{'학습 시나리오 성능 결과 (Inverse Scaled - 실제 수량 기준)':^75}")
        print("="*75)
        print(f"{'시나리오 명':<25} | {'RMSE(개)':<10} | {'MAE(개)':<10} | {'찬스로스 합계'}")
        print("-" * 75)
        for name, r in sorted(results.items()):
            print(f"{name:<25} | {r['RMSE']:<10.4f} | {r['MAE']:<10.4f} | {r['ChanceLoss']:.2f}")
        print("="*75)

        if models:
            best_key = min(results, key=lambda k: results[k]['ChanceLoss'])
            logger.info(f"🏆 베스트 모델 저장: {best_key}")
            winner = models.get('m4')
            if "전년 동월" in best_key: winner = models['m1']
            elif "직전월" in best_key: winner = models['m2']

            import joblib
            model_dir = os.path.join(ai_dir, "models")
            os.makedirs(model_dir, exist_ok=True)
            joblib.dump(winner, os.path.join(model_dir, "advanced_inventory_lgbm.pkl"))
            joblib.dump(self.feature_scaler, os.path.join(model_dir, "feature_scaler.pkl"))
            joblib.dump(self.target_scaler, os.path.join(model_dir, "target_scaler.pkl"))
            logger.info("✅ 우수 모델 및 Scaler 2종(Input/Target)이 저장되었습니다.")

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = HourlyInventoryTrainer(db_url)
    df = trainer.load_and_preprocess()
    if not df.empty: trainer.run_scenarios(df)

if __name__ == "__main__":
    run_pipeline()
