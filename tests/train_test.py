import os
import sys
import logging
import pandas as pd
import numpy as np
import time
import matplotlib.pyplot as plt

# ML Models
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
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
    """찬스로스 방어를 위한 맞춤형 손실 함수 (LightGBM 전용)"""
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
        self.unit_price = 2100 # 도넛 평균 단가

    def load_mart_data(self) -> pd.DataFrame:
        logger.info("Data Mart 테이블 로딩 시작 (전체 매장 대상)...")
        query = 'SELECT * FROM ai_sales_data_mart'
        df = pd.read_sql(query, self.engine)
        df.columns = [c.upper() for c in df.columns]
        df['DATETIME'] = pd.to_datetime(df['DATETIME'])
        logger.info(f"데이터 로드 완료: {len(df):,}건")
        return df

    def calculate_real_chance_loss(self, actuals, predicts):
        """실제 수치 기반 찬스로스 산출 로직"""
        loss_qty = np.maximum(0, actuals - predicts).sum()
        return float(loss_qty * self.unit_price)

    def visualize_model_comparison(self, results_df: pd.DataFrame, save_path: str = 'br-korea-poc-ai/results/model_comparison_chance_loss.png'):
        """계산된 리포트 결과를 바탕으로 시각화 차트 생성"""
        plt.rc('font', family='Malgun Gothic')
        plt.rcParams['axes.unicode_minus'] = False
        
        # '3. 전체 기간' 시나리오 결과 우선 사용
        plot_df = results_df[results_df['Scenario'] == "3. 전체 기간"].copy()
        if plot_df.empty: plot_df = results_df.copy()
        
        model_order = ['RandomForest', 'XGBoost', 'CatBoost', 'LightGBM']
        plot_df['Model'] = pd.Categorical(plot_df['Model'], categories=model_order, ordered=True)
        plot_df = plot_df.sort_values('Model')

        fig, ax1 = plt.subplots(figsize=(12, 7))
        ax1.grid(True, axis='y', linestyle='--', alpha=0.3, color='gray')
        fig.patch.set_facecolor('white')

        colors = ['#cccccc', '#88c9f9', '#4a90e2', '#ff9f43']
        bars = ax1.bar(plot_df['Model'], plot_df['MAE'], color=colors, width=0.5, alpha=0.7, zorder=2)
        ax1.set_ylabel('평균 판매 예측 오차 (MAE, 개)', fontsize=12, fontweight='bold')
        ax1.set_ylim(0, max(plot_df['MAE']) * 1.5)
        ax1.set_xlabel('테스트 알고리즘 (Algorithm)', fontsize=12, fontweight='bold')

        for bar in bars:
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., 0.05, f'{height:.3f}개', 
                     ha='center', va='bottom', fontsize=11, fontweight='bold', color='#333333')

        ax2 = ax1.twinx()
        ax2.plot(plot_df['Model'], plot_df['CHANCE_LOSS_AMT'], color='#e74c3c', marker='o', 
                 linestyle='-', linewidth=3, markersize=10, zorder=3)
        ax2.set_ylabel('데이터 기반 예상 찬스로스 (원)', fontsize=12, fontweight='bold', color='#e74c3c')
        ax2.set_ylim(0, max(plot_df['CHANCE_LOSS_AMT']) * 1.4)

        for i, val in enumerate(plot_df['CHANCE_LOSS_AMT']):
            ax2.text(i, val + (max(plot_df['CHANCE_LOSS_AMT']) * 0.05), f'{int(val/10000):,}만 원', 
                     ha='center', va='bottom', fontsize=12, fontweight='bold', color='#c0392b')

        plt.title('실제 DB 데이터 기반 모델별 예측 정확도 및 찬스로스 비교', fontsize=16, fontweight='bold', pad=30)
        plt.figtext(0.5, 0.01, f"* 찬스로스: 실제 수요 > AI 예측으로 발생한 미판매 기대 수익 (평균 단가 {self.unit_price:,}원 기준)", ha="center", fontsize=10, color="gray")
        fig.tight_layout()
        
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        plt.close()
        logger.info(f"✅ 모델 비교 시각화 차트 저장 완료: {save_path}")

    def run_scenarios(self, df: pd.DataFrame):
        test_end = pd.to_datetime('2026-03-10 23:59:59')
        test_start = test_end - pd.Timedelta(days=30)
        ly_start = pd.to_datetime('2025-03-11 00:00:00')
        prev_start = test_start - pd.Timedelta(days=30)

        test_df = df[(df['DATETIME'] > test_start) & (df['DATETIME'] <= test_end)].copy()
        if test_df.empty:
            logger.error("❌ 평가 데이터가 없습니다.")
            return
        
        y_test_actual = test_df['TARGET_1H_AHEAD'].values.astype(float)
        scenarios = {
            "1. 전년 동월": df[(df['DATETIME'] > ly_start) & (df['DATETIME'] <= ly_start + pd.Timedelta(days=30))].copy(), 
            "2. 직전월": df[(df['DATETIME'] > prev_start) & (df['DATETIME'] <= test_start)].copy(), 
            "3. 전체 기간": df[(df['DATETIME'] >= ly_start) & (df['DATETIME'] <= test_start)].copy()
        }

        eval_results = []
        for scenario_name, train_data in scenarios.items():
            if train_data.empty: continue
            logger.info(f"🚀 [{scenario_name}] 학습/테스트 시작...")
            
            X_tr, y_tr = train_data[self.features].astype(float), train_data[['TARGET_1H_AHEAD']].astype(float)
            X_te = test_df[self.features].astype(float)
            
            f_scaler, t_scaler = StandardScaler(), StandardScaler()
            X_tr_sc = pd.DataFrame(f_scaler.fit_transform(X_tr), columns=self.features)
            X_te_sc = pd.DataFrame(f_scaler.transform(X_te), columns=self.features)
            y_tr_sc = t_scaler.fit_transform(y_tr).flatten()

            # 1. LightGBM (Optimized)
            model_lgb = lgb.LGBMRegressor(random_state=42, verbosity=-1, objective=chance_loss_objective_fixed)
            model_lgb.fit(X_tr_sc, y_tr_sc)
            p_lgb = np.maximum(0, t_scaler.inverse_transform(model_lgb.predict(X_te_sc).reshape(-1, 1)).flatten())
            eval_results.append({'Scenario': scenario_name, 'Model': 'LightGBM', 'MAE': mean_absolute_error(y_test_actual, p_lgb), 'CHANCE_LOSS_AMT': self.calculate_real_chance_loss(y_test_actual, p_lgb)})
            
            # 2. XGBoost
            if xgb:
                model_xgb = xgb.XGBRegressor(random_state=42)
                model_xgb.fit(X_tr_sc, y_tr_sc)
                p_xgb = np.maximum(0, t_scaler.inverse_transform(model_xgb.predict(X_te_sc).reshape(-1, 1)).flatten())
                eval_results.append({'Scenario': scenario_name, 'Model': 'XGBoost', 'MAE': mean_absolute_error(y_test_actual, p_xgb), 'CHANCE_LOSS_AMT': self.calculate_real_chance_loss(y_test_actual, p_xgb)})

            # 3. CatBoost
            if CatBoostRegressor:
                model_cat = CatBoostRegressor(random_seed=42, verbose=0, allow_writing_files=False)
                model_cat.fit(X_tr_sc, y_tr_sc)
                p_cat = np.maximum(0, t_scaler.inverse_transform(model_cat.predict(X_te_sc).reshape(-1, 1)).flatten())
                eval_results.append({'Scenario': scenario_name, 'Model': 'CatBoost', 'MAE': mean_absolute_error(y_test_actual, p_cat), 'CHANCE_LOSS_AMT': self.calculate_real_chance_loss(y_test_actual, p_cat)})

            # 4. RandomForest
            model_rf = RandomForestRegressor(random_state=42, n_estimators=50, n_jobs=-1)
            model_rf.fit(X_tr_sc, y_tr_sc)
            p_rf = np.maximum(0, t_scaler.inverse_transform(model_rf.predict(X_te_sc).reshape(-1, 1)).flatten())
            eval_results.append({'Scenario': scenario_name, 'Model': 'RandomForest', 'MAE': mean_absolute_error(y_test_actual, p_rf), 'CHANCE_LOSS_AMT': self.calculate_real_chance_loss(y_test_actual, p_rf)})

        res_df = pd.DataFrame(eval_results)
        print("\n" + "="*80)
        print(res_df.sort_values(['Scenario', 'MAE']).to_string(index=False))
        print("="*80)
        self.visualize_model_comparison(res_df)

def run_pipeline():
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@127.0.0.1:5435/br_korea_poc")
    trainer = MultiModelScenarioTrainer(db_url)
    df = trainer.load_mart_data()
    if not df.empty: trainer.run_scenarios(df)

if __name__ == "__main__":
    run_pipeline()
