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
        # [Strategy] 실전 배포용 챔피언 시나리오: 전체 기간 100% 학습
        # 이미 백테스팅을 통해 모델 안정성을 검증했으므로, 가장 최신 패턴까지 모두 학습시킵니다.
        logger.info(f"2. 실전 배포용 최종 챔피언 모델 학습 시작 (전체 데이터 100% 활용)")
        
        f_scaler, t_scaler = StandardScaler(), StandardScaler()
        
        # 전체 데이터를 학습셋으로 사용 (테스트셋 분할 없음)
        X_tr = df[self.features].astype(float)
        y_tr = df[['TARGET_1H_AHEAD']].astype(float)
        p_tr = df['PENALTY'].values

        # 스케일링 (Feature Name 유지)
        X_tr_sc = pd.DataFrame(f_scaler.fit_transform(X_tr), columns=self.features)
        y_tr_sc = t_scaler.fit_transform(y_tr).flatten()
        
        # 3. 모델 정의 및 학습 (백테스팅에서 검증된 최적 파라미터 적용)
        lgb_reg = lgb.LGBMRegressor(
            random_state=42, 
            verbosity=-1, 
            learning_rate=0.1,  # 백테스팅에서 최상의 결과를 낸 파라미터 유지
            max_depth=5, 
            n_estimators=100
        )
        
        # 커스텀 상권별 가변 패널티 목적 함수 주입
        lgb_reg.set_params(**{'objective': lambda y_t, y_p: chance_loss_objective_dynamic(y_t, y_p, p_tr)})
        
        start_t = time.time()
        lgb_reg.fit(X_tr_sc, y_tr_sc)
        logger.info(f"모델 전체 데이터 학습 완료 (소요시간: {time.time()-start_t:.1f}초)")

        # 4. 모델 및 스케일러 저장
        # PicklingError 방지: 저장 전 lambda 객체 제거 (예측에는 영향 없음)
        lgb_reg.set_params(objective=None)
        
        joblib.dump(lgb_reg, os.path.join(self.model_dir, 'advanced_inventory_lgbm.joblib'))
        joblib.dump(f_scaler, os.path.join(self.model_dir, 'feature_scaler.joblib'))
        joblib.dump(t_scaler, os.path.join(self.model_dir, 'target_scaler.joblib'))
        joblib.dump(self.penalty_map, os.path.join(self.model_dir, 'penalty_config.joblib'))

        print("\n" + "="*60)
        print(f"{'🏆 실전 배포용 AI 모델(Production Model) 굽기 완료':^60}")
        print("="*60)
        print(f"선정 알고리즘: LightGBM (Dynamic Chance Loss Penalty)")
        print(f"학습 데이터량: {len(df):,} 건 (가용 데이터 100%)")
        print(f"학습 기간    : {df['DATETIME'].min().date()} ~ {df['DATETIME'].max().date()}")
        print("-" * 60)
        print(f"✅ 백테스팅(train_val.py)을 통해 검증된 하이퍼파라미터 적용")
        print(f"✅ 모든 최신 데이터를 학습하여 실시간 추론(Inference) 준비 완료")
        print("-" * 60)
        logger.info(f"✅ 최종 챔피언 모델 및 설정이 저장되었습니다: {self.model_dir}")

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    trainer = ChampionModelTrainer(db_url)
    df = trainer.load_mart_data()
    if not df.empty: trainer.train_champion(df)

if __name__ == "__main__":
    run_pipeline()
