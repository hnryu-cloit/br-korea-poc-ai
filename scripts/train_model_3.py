import os
import sys
import logging
import pandas as pd
import numpy as np
import time

# ML Models
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.ensemble import RandomForestRegressor
import lightgbm as lgb

try:
    from catboost import CatBoostRegressor
except ImportError:
    raise ImportError("CatBoost가 설치되지 않았습니다. 터미널에서 'pip install catboost'를 실행해주세요.")

try:
    from xgboost import XGBRegressor
except ImportError:
    raise ImportError("XGBoost가 설치되지 않았습니다. 터미널에서 'pip install xgboost'를 실행해주세요.")

# DB
from sqlalchemy import create_engine, text

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multi_model_scenarios")

def chance_loss_objective(y_true, y_pred):
    residual = y_pred - y_true
    penalty = 5.0
    grad = np.where(residual < 0, penalty * residual, residual)
    hess = np.where(residual < 0, penalty, 1.0)
    return grad, hess

class MultiModelScenarioTrainer:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        
    def load_and_preprocess(self) -> pd.DataFrame:
        start_t = time.time()
        logger.info("1-1. DB 데이터 로딩 시작 (559만 건)...")
        
        sales_query = text('SELECT masked_stor_cd, item_cd, sale_dt, tmzon_div, sale_qty FROM raw_daily_store_item_tmzon')
        campaign_query = text('''
            SELECT DISTINCT i.item_cd, REPLACE(m.start_dt, '-', '') as start_dt, REPLACE(m.fnsh_dt, '-', '') as fnsh_dt
            FROM raw_campaign_master m 
            JOIN raw_campaign_item i ON m.cmp_cd = i.cmp_cd
        ''')
        
        df_sales = pd.read_sql(sales_query, self.engine)
        df_camp = pd.read_sql(campaign_query, self.engine)
        
        df_sales.columns = [c.upper() for c in df_sales.columns]
        df_camp.columns = [c.upper() for c in df_camp.columns]
        
        logger.info(f"데이터 로드 완료 (소요시간: {time.time()-start_t:.1f}초)")
        
        df_sales['SALE_QTY'] = pd.to_numeric(df_sales['SALE_QTY'], errors='coerce').fillna(0).astype(np.float32)
        df_sales['DATETIME'] = pd.to_datetime(df_sales['SALE_DT'] + df_sales['TMZON_DIV'].astype(str).str.zfill(2), format='%Y%m%d%H')
        df_sales['HOUR'] = df_sales['DATETIME'].dt.hour.astype(np.int8)
        df_sales['WEEKDAY'] = df_sales['DATETIME'].dt.weekday.astype(np.int8)
        df_sales['IS_WEEKEND'] = (df_sales['WEEKDAY'] >= 5).astype(np.int8)
        
        # 1-2. 이벤트(프로모션) 플래그 매핑 (순서 변경: 가중치 계산을 위해 앞으로 이동)
        logger.info("1-2. 이벤트(프로모션) 플래그 매핑 중 (Vectorized)...")
        df_merge = df_sales[['ITEM_CD', 'SALE_DT']].drop_duplicates().merge(df_camp, on='ITEM_CD')
        df_event = df_merge[(df_merge['SALE_DT'] >= df_merge['START_DT']) & (df_merge['SALE_DT'] <= df_merge['FNSH_DT'])]
        df_event_map = df_event[['ITEM_CD', 'SALE_DT']].drop_duplicates()
        df_event_map['IS_EVENT'] = 1
        
        df_sales = df_sales.merge(df_event_map, on=['ITEM_CD', 'SALE_DT'], how='left')
        df_sales['IS_EVENT'] = df_sales['IS_EVENT'].fillna(0).astype(np.int8)

        # 1-3. 가중치 적용 4주 평균 패턴 계산
        logger.info("1-3. 과거 프로모션 가중치 적용 4주 평균 계산 중...")
        df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR', 'DATETIME'])
        g_sales = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['SALE_QTY']
        g_event = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['IS_EVENT']

        # 과거 4주 데이터 및 이벤트 여부 추출
        s1, s2, s3, s4 = g_sales.shift(1).fillna(0), g_sales.shift(2).fillna(0), g_sales.shift(3).fillna(0), g_sales.shift(4).fillna(0)
        e1, e2, e3, e4 = g_event.shift(1).fillna(0), g_event.shift(2).fillna(0), g_event.shift(3).fillna(0), g_event.shift(4).fillna(0)

        # 고객사 요청 가중치 로직 (하드코딩 예시 반영)
        # Normal: 0.35, Full Promo: 0.1
        def get_weight(e_flag):
            return np.where(e_flag == 1, 0.1, 0.35) 

        w1, w2, w3, w4 = get_weight(e1), get_weight(e2), get_weight(e3), get_weight(e4)
        
        # 가중치 합으로 나누어 정규화 (합이 1이 되도록)
        w_sum = w1 + w2 + w3 + w4
        df_sales['HIST_4W_AVG'] = (s1*w1 + s2*w2 + s3*w3 + s4*w4) / w_sum
        df_sales['HIST_4W_AVG'] = df_sales['HIST_4W_AVG'].replace(0, np.nan).bfill().fillna(0).astype(np.float32)

        logger.info("1-4. 이상치 보정 및 타겟(1H Ahead) 생성 중...")
        upper_bound = df_sales['SALE_QTY'].quantile(0.99)
        df_sales.loc[(df_sales['IS_EVENT'] == 0) & (df_sales['SALE_QTY'] > upper_bound), 'SALE_QTY'] = upper_bound
        
        df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'DATETIME'])
        df_sales['TARGET_1H_AHEAD'] = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY'].shift(-1)
        df_sales = df_sales.dropna(subset=['TARGET_1H_AHEAD'])

        logger.info(f"✅ 전처리 완료! (총 소요시간: {time.time()-start_t:.1f}초, 데이터: {len(df_sales)}행)")
        return df_sales

    def run_scenarios(self, df: pd.DataFrame):
        test_end = pd.to_datetime('2026-03-10 23:59:59')
        test_start = test_end - pd.Timedelta(days=30)
        
        # 데이터 시작일이 2025-03-11이므로, 전년 동월 대용으로 최초 30일 데이터 사용
        ly_start = pd.to_datetime('2025-03-11 00:00:00')
        ly_end = ly_start + pd.Timedelta(days=30)
        
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
        actual_preds_storage = {"LightGBM": {}, "CatBoost": {}, "XGBoost": {}, "RandomForest": {}}

        def get_scaled_data(train_data, test_data):
            f_scaler, t_scaler = StandardScaler(), StandardScaler()
            X_tr, y_tr = train_data[self.features].copy(), train_data[['TARGET_1H_AHEAD']].copy()
            X_te, y_te = test_data[self.features].copy(), test_data[['TARGET_1H_AHEAD']].copy()
            
            for c in self.features:
                X_tr[c] = X_tr[c].astype(float)
                X_te[c] = X_te[c].astype(float)
            
            y_tr['TARGET_1H_AHEAD'] = y_tr['TARGET_1H_AHEAD'].astype(float)
            y_te['TARGET_1H_AHEAD'] = y_te['TARGET_1H_AHEAD'].astype(float)

            X_tr.loc[:, self.features] = f_scaler.fit_transform(X_tr)
            X_te.loc[:, self.features] = f_scaler.transform(X_te)
            
            y_tr_scaled = t_scaler.fit_transform(y_tr).flatten()
            y_te_scaled = t_scaler.transform(y_te).flatten()
            
            return X_tr, y_tr_scaled, X_te, y_te_scaled, t_scaler

        def record_metrics(model_name, scenario_name, preds_scaled, t_scaler):
            preds_actual = t_scaler.inverse_transform(preds_scaled.reshape(-1, 1)).flatten()
            rmse = np.sqrt(mean_squared_error(y_test_actual, preds_actual))
            mae = mean_absolute_error(y_test_actual, preds_actual)
            chance_loss = np.sum(np.maximum(0, y_test_actual - preds_actual))
            eval_results.append({'Model': model_name, 'Scenario': scenario_name, 'RMSE': rmse, 'MAE': mae, 'ChanceLoss': chance_loss})
            if model_name in actual_preds_storage: actual_preds_storage[model_name][scenario_name] = preds_actual

        tscv = TimeSeriesSplit(n_splits=3)
        for scenario_name, train_data in scenarios.items():
            if train_data.empty: continue
            X_tr, y_tr, X_te, y_te_scaled, t_scaler = get_scaled_data(train_data, test_df)

            # --- LightGBM ---
            logger.info(f"학습 중: LightGBM - {scenario_name}")
            lgb_reg = lgb.LGBMRegressor(random_state=42, n_estimators=100, verbosity=-1)
            lgb_reg.set_params(**{'objective': chance_loss_objective})
            lgb_search = RandomizedSearchCV(lgb_reg, {'learning_rate': [0.01, 0.05], 'max_depth': [5, 7]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
            lgb_search.fit(X_tr, y_tr)
            record_metrics("LightGBM", scenario_name, lgb_search.predict(X_te), t_scaler)

            # --- CatBoost ---
            logger.info(f"학습 중: CatBoost - {scenario_name}")
            cat_reg = CatBoostRegressor(iterations=100, random_seed=42, verbose=0, loss_function='MAE', allow_writing_files=False)
            cat_search = RandomizedSearchCV(cat_reg, {'learning_rate': [0.01, 0.05], 'depth': [4, 6]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
            cat_search.fit(X_tr, y_tr)
            record_metrics("CatBoost", scenario_name, cat_search.predict(X_te), t_scaler)

            # --- XGBoost ---
            logger.info(f"학습 중: XGBoost - {scenario_name}")
            xgb_reg = XGBRegressor(random_state=42, n_estimators=100, verbosity=0)
            xgb_search = RandomizedSearchCV(xgb_reg, {'learning_rate': [0.01, 0.05], 'max_depth': [4, 6]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
            xgb_search.fit(X_tr, y_tr)
            record_metrics("XGBoost", scenario_name, xgb_search.predict(X_te), t_scaler)

            # --- RandomForest ---
            logger.info(f"학습 중: RandomForest - {scenario_name}")
            rf_reg = RandomForestRegressor(random_state=42, n_estimators=50, n_jobs=-1)
            rf_search = RandomizedSearchCV(rf_reg, {'max_depth': [10, 20]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
            rf_search.fit(X_tr, y_tr)
            record_metrics("RandomForest", scenario_name, rf_search.predict(X_te), t_scaler)

        # 앙상블 추가
        for m_name in ["LightGBM", "CatBoost", "XGBoost", "RandomForest"]:
            if "1. 전년 동월" in actual_preds_storage[m_name] and "2. 직전월" in actual_preds_storage[m_name]:
                ens_actual = (actual_preds_storage[m_name]["1. 전년 동월"] + actual_preds_storage[m_name]["2. 직전월"]) / 2.0
                eval_results.append({'Model': m_name, 'Scenario': "4. 전년동월+직전월 앙상블", 'RMSE': np.sqrt(mean_squared_error(y_test_actual, ens_actual)), 'MAE': mean_absolute_error(y_test_actual, ens_actual), 'ChanceLoss': np.sum(np.maximum(0, y_test_actual - ens_actual))})

        # 결과 출력
        print("\n" + "="*85)
        print(f"{'다중 모델 & 기간 조합 성능 비교 (최종 결과)':^85}")
        print("="*85)
        print(f"{'알고리즘 명':<15} | {'학습 기간 조합':<20} | {'RMSE(개)':<10} | {'MAE(개)':<10} | {'찬스로스 합계'}")
        print("-" * 85)
        for r in sorted(eval_results, key=lambda x: (x['Model'], x['Scenario'])):
            print(f"{r['Model']:<15} | {r['Scenario']:<20} | {r['RMSE']:<10.4f} | {r['MAE']:<10.4f} | {r['ChanceLoss']:.2f}")
        print("="*85)

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = MultiModelScenarioTrainer(db_url)
    df = trainer.load_and_preprocess()
    if not df.empty: trainer.run_scenarios(df)

if __name__ == "__main__":
    run_pipeline()
