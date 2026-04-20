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
import lightgbm as lgb

from sqlalchemy import create_engine

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("business_value_backtesting")

def chance_loss_objective_dynamic(y_true, y_pred, penalties):
    """지점별 클러스터 특성에 따른 가변 패널티 적용 손실 함수"""
    residual = y_pred - y_true
    # penalties는 y_true와 동일한 길이를 가진 배열 (각 row별 패널티 수치)
    grad = np.where(residual < 0, penalties * residual, residual)
    hess = np.where(residual < 0, penalties, 1.0)
    return grad, hess

class ExpandingWindowBacktester:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.features = ['HOUR', 'WEEKDAY', 'IS_WEEKEND', 'HIST_4W_AVG', 'IS_EVENT', 'SALE_QTY']
        self.MARGIN = 0.65
        self.COST = 0.35
        
        # [Optimized] 리포트 결과 기반 클러스터별 최적 패널티 재매핑
        self.penalty_map = {
            0: 2.6,  # 로드샵: 매출 확보를 위해 소폭 상향 (기존 2.0 -> 2.6)
            1: 2.2,  # 아침상권: 적자 폭이 크므로 효율 중시 하향 (기존 3.5 -> 2.2)
            2: 2.4,  # 배달상권: 수익 안정화를 위해 소폭 하향 (기존 3.0 -> 2.4)
            3: 2.0,  # 퇴근상권: 적자 방어 최우선 하향 (기존 3.5 -> 2.0)
            4: 2.2   # 주말상권: 밸런스 유지를 위해 소폭 상향 (기존 2.0 -> 2.2)
        }
        
    def load_mart_data(self) -> pd.DataFrame:
        logger.info("Data Mart 테이블 로딩 시작 (전체 매장 대상)...")
        query = 'SELECT * FROM ai_sales_data_mart'
        df = pd.read_sql(query, self.engine)
        df.columns = [c.upper() for c in df.columns]
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        
        # [Optimized] 상권 그룹별 고정 패널티 할당
        df['PENALTY'] = df['STORE_CLUSTER'].map(self.penalty_map).fillna(2.2)
        
        return df

    def run_backtesting(self, df: pd.DataFrame, num_folds=3):
        base_test_end = pd.to_datetime('2026-03-10 23:59:59')
        full_train_start = pd.to_datetime('2025-03-11 00:00:00')
        all_store_results = []
        
        print("\n" + "="*120)
        print(f"{'🚀 [전략 B+ 고도화] 상권별 가변 패널티 기반 6개월 손익 시뮬레이션':^120}")
        print("="*120)

        for fold in range(num_folds):
            test_end = base_test_end - pd.Timedelta(days=30 * fold)
            test_start = test_end - pd.Timedelta(days=30)
            
            train_df = df[(df['DATETIME'] >= full_train_start) & (df['DATETIME'] <= test_start)].copy()
            test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
            
            if len(train_df) < 10000 or len(test_df) < 1000: continue
            logger.info(f"Fold {fold+1} 학습 중 ({test_start.date()} ~ )...")

            y_actual = test_df['TARGET_1H_AHEAD'].values
            y_baseline = test_df['HIST_4W_AVG'].values
            
            f_scaler, t_scaler = StandardScaler(), StandardScaler()
            # X_tr, y_tr 데이터 준비 (패널티 컬럼 포함)
            X_tr, y_tr = train_df[self.features].astype(float), train_df[['TARGET_1H_AHEAD']].astype(float)
            X_te = test_df[self.features].astype(float)
            p_tr = train_df['PENALTY'].values # 학습셋용 패널티 배열
            
            X_tr_sc = pd.DataFrame(f_scaler.fit_transform(X_tr), columns=self.features)
            X_te_sc = pd.DataFrame(f_scaler.transform(X_te), columns=self.features)
            y_tr_sc = t_scaler.fit_transform(y_tr).flatten()
            
            # [Fix] CV(교차검증) 내부 분할로 인한 패널티 배열 크기 불일치 해결
            # 이미 시계열 백테스팅 중이므로 내부 CV 없이 직접 모델 학습
            lgb_reg = lgb.LGBMRegressor(
                random_state=42, 
                verbosity=-1, 
                learning_rate=0.1, 
                max_depth=5, 
                n_estimators=100
            )
            lgb_reg.set_params(**{'objective': lambda y_t, y_p: chance_loss_objective_dynamic(y_t, y_p, p_tr)})
            lgb_reg.fit(X_tr_sc, y_tr_sc)
            
            # 예측
            preds = t_scaler.inverse_transform(lgb_reg.predict(X_te_sc).reshape(-1, 1)).flatten()
            preds = np.maximum(0, preds)

            # 지점별 성과 분석
            analysis_df = test_df[['MASKED_STOR_CD', 'STORE_CLUSTER']].copy()
            analysis_df['ACTUAL'] = y_actual
            analysis_df['BASELINE'] = y_baseline
            analysis_df['AI_PRED'] = preds
            
            store_groups = analysis_df.groupby(['MASKED_STOR_CD', 'STORE_CLUSTER']).apply(lambda x: pd.Series({
                'Base_CL': np.sum(np.maximum(0, x['ACTUAL'] - x['BASELINE'])),
                'Base_SP': np.sum(np.maximum(0, x['BASELINE'] - x['ACTUAL'])),
                'AI_CL': np.sum(np.maximum(0, x['ACTUAL'] - x['AI_PRED'])),
                'AI_SP': np.sum(np.maximum(0, x['AI_PRED'] - x['ACTUAL'])),
                'MAE': mean_absolute_error(x['ACTUAL'], x['AI_PRED'])
            }), include_groups=False).reset_index()
            
            store_groups['CL_Saved'] = store_groups['Base_CL'] - store_groups['AI_CL']
            store_groups['Extra_Waste'] = store_groups['AI_SP'] - store_groups['Base_SP']
            store_groups['Net_Profit_Index'] = (store_groups['CL_Saved'] * self.MARGIN) - (store_groups['Extra_Waste'] * self.COST)
            
            all_store_results.append(store_groups)

        full_results = pd.concat(all_store_results)
        store_summary = full_results.groupby(['MASKED_STOR_CD', 'STORE_CLUSTER']).agg({
            'MAE': 'mean',
            'CL_Saved': 'sum',
            'Extra_Waste': 'sum',
            'Net_Profit_Index': 'sum'
        }).reset_index().sort_values(by='Net_Profit_Index', ascending=False)

        print("\n[PART 1. 상권 그룹별 AI 도입 성과 통합 리포트]")
        print("-" * 110)
        print(f"{'지점코드':<12} | {'그룹':<4} | {'평균 MAE':<8} | {'찬스로스 감소':<12} | {'추가 폐기':<10} | {'이익개선 지수'}")
        print("-" * 110)
        for _, r in store_summary.iterrows():
            print(f"{r['MASKED_STOR_CD']:<12} | {int(r['STORE_CLUSTER']):<4} | {r['MAE']:<8.4f} | {r['CL_Saved']:<12,.1f} | {r['Extra_Waste']:<10,.1f} | {r['Net_Profit_Index']:+,.1f}")
        
        print("\n" + "="*120)
        print(f"{'PART 2. 전체 상권 통합 요약 성적표':^120}")
        print("-" * 120)
        print(f"최종 순이익 개선지수 합계 : {store_summary['Net_Profit_Index'].sum():+,.1f}")
        print("="*120)

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    tester = ExpandingWindowBacktester(db_url)
    df = tester.load_mart_data()
    if not df.empty: tester.run_backtesting(df, num_folds=3)

if __name__ == "__main__":
    run_pipeline()
