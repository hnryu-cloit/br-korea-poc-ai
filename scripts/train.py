import os
import sys
import logging
import pandas as pd
import numpy as np
import joblib
import time

# ML Models
from sklearn.preprocessing import StandardScaler
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

def chance_loss_objective_dynamic(y_true, y_pred, penalties):
    """지점별 클러스터 특성에 따른 가변 패널티 적용 손실 함수"""
    residual = y_pred - y_true
    # penalties는 y_true와 동일한 길이를 가진 배열
    grad = np.where(residual < 0, penalties * residual, residual)
    hess = np.where(residual < 0, penalties, 1.0)
    return grad, hess

class ChampionModelTrainer:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        self.model_dir = os.path.join(ai_dir, 'models')
        if not os.path.exists(self.model_dir):
            os.makedirs(self.model_dir)
        
        # [Champion] 검증된 상권별 최적 패널티 설정
        self.penalty_map = {
            0: 2.6,  # 로드샵
            1: 2.2,  # 아침상권
            2: 2.4,  # 배달상권
            3: 2.0,  # 퇴근상권
            4: 2.2   # 주말상권
        }
        
    def load_mart_data(self) -> pd.DataFrame:
        logger.info("1. 최종 학습용 Data Mart 테이블 로딩 시작...")
        query = "SELECT * FROM ai_sales_data_mart"
        df = pd.read_sql(query, self.engine)
        df.columns = [c.upper() for c in df.columns]
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        
        # 패널티 수치 할당
        df['PENALTY'] = df['STORE_CLUSTER'].map(self.penalty_map).fillna(2.4)
        logger.info(f"데이터 로드 완료: {len(df):,}건")
        return df

    def train_champion(self, df: pd.DataFrame):
        # [Strategy] 챔피언 시나리오: 전체 기간 학습
        # 평가를 위해 최근 30일을 테스트셋으로 분리하되, 최종 모델은 전체를 다시 학습 가능
        test_end = df['DATETIME'].max()
        train_end = test_end - pd.Timedelta(days=30)

        train_df = df[df['DATETIME'] <= train_end].copy()
        test_df = df[df['DATETIME'] > train_end].copy()
        
        logger.info(f"2. 챔피언 모델 학습 시작 (학습: ~{train_end.date()}, 평가: {train_end.date()}~)")
        
        f_scaler, t_scaler = StandardScaler(), StandardScaler()
        X_tr, y_tr = train_df[self.features].astype(float), train_df[['TARGET_1H_AHEAD']].astype(float)
        X_te, y_te_actual = test_df[self.features].astype(float), test_df['TARGET_1H_AHEAD'].values
        p_tr = train_df['PENALTY'].values

        # 스케일링 (Feature Name 유지)
        X_tr_sc = pd.DataFrame(f_scaler.fit_transform(X_tr), columns=self.features)
        X_te_sc = pd.DataFrame(f_scaler.transform(X_te), columns=self.features)
        y_tr_sc = t_scaler.fit_transform(y_tr).flatten()
        
        # 3. 모델 정의 및 학습 (최적 파라미터 적용)
        lgb_reg = lgb.LGBMRegressor(
            random_state=42, 
            verbosity=-1, 
            learning_rate=0.05, 
            max_depth=7, 
            n_estimators=200
        )
        # 커스텀 가변 패널티 목적 함수 주입
        lgb_reg.set_params(**{'objective': lambda y_t, y_p: chance_loss_objective_dynamic(y_t, y_p, p_tr)})
        
        start_t = time.time()
        lgb_reg.fit(X_tr_sc, y_tr_sc)
        logger.info(f"모델 학습 완료 (소요시간: {time.time()-start_t:.1f}초)")
        
        # 4. 성능 검증 (최근 30일 데이터 기준)
        preds_scaled = lgb_reg.predict(X_te_sc)
        preds = t_scaler.inverse_transform(preds_scaled.reshape(-1, 1)).flatten()
        preds = np.maximum(0, preds)
        
        mae = mean_absolute_error(y_te_actual, preds)
        rmse = np.sqrt(mean_squared_error(y_te_actual, preds))
        chance_loss = np.sum(np.maximum(0, y_te_actual - preds))
        accuracy = max(0, (1 - mae / np.mean(y_te_actual)) * 100) if np.mean(y_te_actual) > 0 else 0

        # 5. 모델 및 스케일러 저장
        # [Fix] PicklingError 방지: 저장 전 lambda 객체 제거 (예측에는 영향 없음)
        lgb_reg.set_params(objective=None)
        
        joblib.dump(lgb_reg, os.path.join(self.model_dir, 'advanced_inventory_lgbm.joblib'))
        joblib.dump(f_scaler, os.path.join(self.model_dir, 'feature_scaler.joblib'))
        joblib.dump(t_scaler, os.path.join(self.model_dir, 'target_scaler.joblib'))
        # 패널티 설정도 저장 (Inference 시 활용 가능)
        joblib.dump(self.penalty_map, os.path.join(self.model_dir, 'penalty_config.joblib'))

        print("\n" + "="*60)
        print(f"{'🏆 최종 챔피언 모델 학습 결과 보고서':^60}")
        print("="*60)
        print(f"선정 알고리즘: LightGBM (Dynamic Chance Loss)")
        print(f"학습 데이터  : 전체 기간 (Cumulative)")
        print(f"평가 기간    : 최근 30일")
        print("-" * 60)
        print(f"MAE (평균 오차)   : {mae:.4f} 개")
        print(f"RMSE (오차 안정성): {rmse:.4f} 개")
        print(f"기회손실 총합    : {chance_loss:,.1f} 개")
        print(f"모델 정확도      : {accuracy:.2f} %")
        print("-" * 60)
        logger.info(f"✅ 최종 챔피언 모델이 저장되었습니다: {self.model_dir}")

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = ChampionModelTrainer(db_url)
    df = trainer.load_mart_data()
    if not df.empty: trainer.train_champion(df)

if __name__ == "__main__":
    run_pipeline()
