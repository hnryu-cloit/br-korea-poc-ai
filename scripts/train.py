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

# DB
from sqlalchemy import create_engine

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("champion_model_trainer")

def chance_loss_objective(y_true, y_pred):
    residual = y_pred - y_true
    penalty = 5.0
    grad = np.where(residual < 0, penalty * residual, residual)
    hess = np.where(residual < 0, penalty, 1.0)
    return grad, hess

class ChampionModelTrainer:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        self.model_dir = os.path.join(ai_dir, 'models')
        if not os.path.exists(self.model_dir):
            os.makedirs(self.model_dir)
        
    def load_mart_data(self) -> pd.DataFrame:
        logger.info("Data Mart 테이블(ai_sales_data_mart) 로딩 시작...")
        query = "SELECT * FROM ai_sales_data_mart"
        df = pd.read_sql(query, self.engine)
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        logger.info(f"데이터 로드 완료: {len(df)}건")
        return df

    def train_champion(self, df: pd.DataFrame):
        test_end = pd.to_datetime('2026-03-10 23:59:59')
        test_start = test_end - pd.Timedelta(days=30)
        full_train_start = pd.to_datetime('2025-03-11 00:00:00')

        train_df = df[(df['DATETIME'] >= full_train_start) & (df['DATETIME'] <= test_start)].copy()
        test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
        
        y_test_actual = test_df['TARGET_1H_AHEAD'].values
        
        f_scaler, t_scaler = StandardScaler(), StandardScaler()
        X_tr, y_tr = train_df[self.features].copy(), train_df[['TARGET_1H_AHEAD']].copy()
        X_te, y_te = test_df[self.features].copy(), test_df[['TARGET_1H_AHEAD']].copy()
        
        for c in self.features:
            X_tr[c], X_te[c] = X_tr[c].astype(float), X_te[c].astype(float)
            
        X_tr.loc[:, self.features] = f_scaler.fit_transform(X_tr)
        X_te.loc[:, self.features] = f_scaler.transform(X_te)
        y_tr_scaled = t_scaler.fit_transform(y_tr).flatten()
        
        logger.info("==> 챔피언 모델(LightGBM) 학습 및 하이퍼파라미터 튜닝 중...")
        lgb_reg = lgb.LGBMRegressor(random_state=42, n_estimators=100, verbosity=-1)
        lgb_reg.set_params(**{'objective': chance_loss_objective})
        
        tscv = TimeSeriesSplit(n_splits=3)
        search = RandomizedSearchCV(lgb_reg, {'learning_rate': [0.01, 0.05], 'max_depth': [5, 7]}, n_iter=2, cv=tscv, scoring='neg_mean_absolute_error', n_jobs=-1, random_state=42)
        search.fit(X_tr, y_tr_scaled)
        
        best_model = search.best_estimator_
        
        joblib.dump(best_model, os.path.join(self.model_dir, 'advanced_inventory_lgbm.joblib'))
        joblib.dump(f_scaler, os.path.join(self.model_dir, 'feature_scaler.joblib'))
        joblib.dump(t_scaler, os.path.join(self.model_dir, 'target_scaler.joblib'))
        
        preds_actual = t_scaler.inverse_transform(best_model.predict(X_te).reshape(-1, 1)).flatten()
        rmse = np.sqrt(mean_squared_error(y_test_actual, preds_actual))
        mae = mean_absolute_error(y_test_actual, preds_actual)
        chance_loss = np.sum(np.maximum(0, y_test_actual - preds_actual))
        accuracy = max(0, (1 - mae / np.mean(y_test_actual)) * 100) if np.mean(y_test_actual) > 0 else 0
        
        print("\n" + "="*60)
        print(f"{'🏆 최종 챔피언 모델 학습 결과 보고서':^60}")
        print("="*60)
        print(f"선정 알고리즘: LightGBM (Chance Loss Objective)")
        print(f"평가 기간: {test_start.date()} ~ {test_end.date()}")
        print("-" * 60)
        print(f"RMSE (오차 안정성) : {rmse:.4f} 개")
        print(f"MAE  (평균 오차)   : {mae:.4f} 개")
        print(f"기회손실 합계      : {chance_loss:.2f} 개")
        print(f"모델 정확도        : {accuracy:.2f} %")
        print("-" * 60)
        logger.info(f"✅ 모델 파일이 저장되었습니다: {self.model_dir}")

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = ChampionModelTrainer(db_url)
    df = trainer.load_mart_data()
    if not df.empty: trainer.train_champion(df)

if __name__ == "__main__":
    run_pipeline()
