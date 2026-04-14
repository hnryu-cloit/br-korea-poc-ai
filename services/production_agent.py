from __future__ import annotations
import os
import joblib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from common.logger import init_logger

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    from sklearn.ensemble import RandomForestRegressor
    HAS_LGB = False

logger = init_logger("production_agent")


# ==========================================
# 1. 재고 역산 엔진 (InventoryReversalEngine)
# ==========================================
class InventoryReversalEngine:
    """
    가이드라인 1: 재고 역산 엔진 (Core Logic)
    기초 재고, 생산(입고), 매출(출고) 데이터를 결합하여 가상 재고 흐름을 생성합니다.
    """
    def __init__(self, inventory_df: pd.DataFrame, production_df: pd.DataFrame, sales_df: pd.DataFrame):
        self.inventory_df = inventory_df
        self.production_df = production_df
        self.sales_df = sales_df

    def get_estimated_stock(self, store_cd: str, item_cd: str, target_date: str):
        """5분 단위 추정 재고 테이블 생성 (영업 시간 동적 추정 및 음수 보정)"""
        logger.info(f"Calculating stock flow for Store: {store_cd}, Item: {item_cd}, Date: {target_date}")

        base_stock_row = self.inventory_df[
            (self.inventory_df['MASKED_STOR_CD'].astype(str) == str(store_cd)) & 
            (self.inventory_df['ITEM_CD'].astype(str) == str(item_cd)) &
            (self.inventory_df['STOCK_DT'].astype(str) == str(target_date))
        ]
        base_stock = pd.to_numeric(base_stock_row['STOCK_QTY'], errors='coerce').fillna(0).sum() if not base_stock_row.empty else 0

        prod_data = self.production_df[
            (self.production_df['MASKED_STOR_CD'].astype(str) == str(store_cd)) & 
            (self.production_df['ITEM_CD'].astype(str) == str(item_cd)) &
            (self.production_df['PROD_DT'].astype(str) == str(target_date))
        ].copy()
        
        def map_prod_time(dgre):
            try:
                hour = 8 + (int(dgre) - 1) * 2
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=9)

        if not prod_data.empty:
            prod_data['timestamp'] = prod_data['PROD_DGRE'].apply(map_prod_time)

        # 해당 지점의 당일 전체 판매 데이터를 가져와 영업 시간 추정
        store_all_sales = self.sales_df[
            (self.sales_df['MASKED_STOR_CD'].astype(str) == str(store_cd)) & 
            (self.sales_df['SALE_DT'].astype(str) == str(target_date))
        ].copy()
        
        # 영업 시간 파악 (판매 기록이 있는 최소~최대 시간대)
        if not store_all_sales.empty and 'TMZON_DIV' in store_all_sales.columns:
            store_all_sales['TMZON_DIV'] = pd.to_numeric(store_all_sales['TMZON_DIV'], errors='coerce').fillna(-1).astype(int)
            valid_sales = store_all_sales[store_all_sales['TMZON_DIV'] >= 0]
            
            if not valid_sales.empty:
                min_hour = valid_sales['TMZON_DIV'].min()
                max_hour = valid_sales['TMZON_DIV'].max() + 1 # 마감 시간 여유 1시간
            else:
                min_hour, max_hour = 8, 23
        else:
            min_hour, max_hour = 8, 23 # 기본 영업시간

        # 너무 좁은 범위 방지 (최소 6시간 영업 보장)
        if max_hour - min_hour < 6:
            min_hour = max(0, min_hour - 2)
            max_hour = min(24, max_hour + 2)

        sales_data = store_all_sales[store_all_sales['ITEM_CD'].astype(str) == str(item_cd)].copy()
        
        def map_sale_time(tmzon):
            try:
                hour = int(tmzon)
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, '%Y%m%d')

        if not sales_data.empty:
            sales_data['timestamp'] = sales_data['TMZON_DIV'].apply(map_sale_time)

        # 동적으로 계산된 영업 시간에 맞게 타임라인 생성
        start_time = datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=int(min_hour))
        end_time = datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=int(max_hour))
        
        # 만약 시작/종료 시간이 자정을 넘기거나 비정상일 경우 보정
        if end_time <= start_time:
            end_time = start_time + timedelta(hours=12)

        timeline = pd.date_range(start=start_time, end=end_time, freq='5min', inclusive='left')
        
        df_flow = pd.DataFrame(index=timeline)
        df_flow['in_qty'] = 0.0
        df_flow['out_qty'] = 0.0

        for _, row in prod_data.iterrows():
            ts = row['timestamp']
            if ts in df_flow.index:
                qty = pd.to_numeric(row['PROD_QTY'], errors='coerce')
                if pd.notna(qty):
                    df_flow.at[ts, 'in_qty'] += qty

        for _, row in sales_data.iterrows():
            ts = row['timestamp']
            qty = pd.to_numeric(row['SALE_QTY'], errors='coerce')
            if pd.notna(qty):
                for i in range(12):
                    slot = ts + timedelta(minutes=i*5)
                    if slot in df_flow.index:
                        df_flow.at[slot, 'out_qty'] += (qty / 12)

        df_flow['stock_change'] = df_flow['in_qty'] - df_flow['out_qty']
        
        # [핵심] 누적 재고 계산 및 '생산 기록 역추적(Back-tracking)' 로직
        # 생산 데이터(PROD_DTL)가 누락되어 판매량(out_qty) 때문에 재고가 마이너스로 뚫릴 위기에 처하면,
        # AI가 "이 시점에 최소 이만큼은 생산(in_qty)했을 것이다"라고 가상의 생산 기록을 복원해 냅니다.
        current_stock = base_stock
        estimated_stocks = []
        
        for ts, row in df_flow.iterrows():
            change = row['stock_change']
            # 현재 스텝의 재고 변화 적용
            next_stock = current_stock + change
            
            # 만약 재고가 0 미만으로 떨어진다면? -> 생산 데이터가 누락된 것임!
            if next_stock < 0:
                # 부족한 수량 + 여유 버퍼(안전재고 20%)만큼 가상의 생산(Virtual Production)이 일어났다고 추론
                # 정수 단위 생산을 위해 올림(ceil) 처리
                shortage = abs(next_stock)
                virtual_prod_qty = int(np.ceil(shortage * 1.2)) 
                
                # 가상 생산량을 현재 시간대의 입고(in_qty)에 강제 주입
                df_flow.at[ts, 'in_qty'] += virtual_prod_qty
                # stock_change 다시 계산
                change = df_flow.at[ts, 'in_qty'] - df_flow.at[ts, 'out_qty']
                
                logger.debug(f"[역추적 감지] {ts.strftime('%H:%M')} 재고 부족({next_stock:.1f}). 가상 생산량 {virtual_prod_qty}개 복원 주입.")
                
                current_stock += change # 보정된 변화량으로 재고 재계산
            else:
                current_stock = next_stock
                
            estimated_stocks.append(current_stock)
            
        df_flow['estimated_stock'] = estimated_stocks
        
        return df_flow


# ==========================================
# 2. 기회 손실 서비스 (ChanceLossService)
# ==========================================
class ChanceLossService:
    """영업 시간 중 매출이 '0'인 구간을 추출하여 유실된 수량(Chance Loss)을 산출"""
    def __init__(self, historical_sales_df: pd.DataFrame, campaign_df: pd.DataFrame = None):
        self.historical_sales_df = historical_sales_df
        self.campaign_df = campaign_df if campaign_df is not None else pd.DataFrame()

    def calculate_chance_loss(self, store_cd: str, item_cd: str, target_date: str, current_sales_df: pd.DataFrame) -> dict:
        logger.info(f"Calculating chance loss for Store: {store_cd}, Item: {item_cd}, Date: {target_date}")
        operating_hours = [f"{i:02d}" for i in range(8, 24)]
        today_sales = current_sales_df[
            (current_sales_df['MASKED_STOR_CD'] == store_cd) & 
            (current_sales_df['ITEM_CD'] == item_cd) &
            (current_sales_df['SALE_DT'] == target_date)
        ].copy()
        
        active_hours = today_sales['TMZON_DIV'].astype(str).str.zfill(2).tolist() if not today_sales.empty else []
        zero_sales_hours = [h for h in operating_hours if h not in active_hours]
        
        if not zero_sales_hours:
            return {"total_chance_loss_qty": 0, "details": []}

        target_dt = datetime.strptime(target_date, '%Y%m%d')
        start_hist = target_dt - timedelta(weeks=4)
        end_hist = target_dt - timedelta(days=1)
        target_weekday = target_dt.weekday()
        
        hist_data = self.historical_sales_df[
            (self.historical_sales_df['MASKED_STOR_CD'] == store_cd) &
            (self.historical_sales_df['ITEM_CD'] == item_cd)
        ].copy()
        
        if not hist_data.empty:
            hist_data['sale_dt_dt'] = pd.to_datetime(hist_data['SALE_DT'], format='%Y%m%d')
            hist_data = hist_data[
                (hist_data['sale_dt_dt'] >= start_hist) & 
                (hist_data['sale_dt_dt'] <= end_hist) &
                (hist_data['sale_dt_dt'].dt.weekday == target_weekday)
            ]

        is_campaign = False
        if not self.campaign_df.empty:
            is_campaign = self.campaign_df[
                (self.campaign_df['START_DT'] <= target_date) & 
                (self.campaign_df['FNSH_DT'] >= target_date)
            ].shape[0] > 0
        campaign_weight = 1.2 if is_campaign else 1.0

        total_loss = 0
        details = []

        for hour in zero_sales_hours:
            avg_qty = 0.0
            if not hist_data.empty:
                hour_data = hist_data[
                    (hist_data['TMZON_DIV'].astype(str).str.zfill(2) == hour) |
                    (hist_data['TMZON_DIV'].astype(str) == str(int(hour)))
                ]
                if not hour_data.empty:
                    avg_qty = hour_data['SALE_QTY'].mean()
            
            adjusted_qty = round(avg_qty * campaign_weight, 1)
            total_loss += adjusted_qty
            details.append({
                "time_zone": hour,
                "historical_avg_qty": round(avg_qty, 1),
                "applied_weight": campaign_weight,
                "estimated_loss_qty": adjusted_qty
            })

        return {
            "total_chance_loss_qty": round(total_loss, 1),
            "is_campaign_active": is_campaign,
            "details": details
        }


# ==========================================
# 3. ML 기반 예측기 (InventoryPredictor)
# ==========================================
class InventoryPredictor:
    """[Balanced High-Precision] 안정성과 정확도를 모두 잡은 최종 예측 엔진"""
    def __init__(self, model_dir: Optional[str] = None):
        self.model = None
        if model_dir is None:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.model_dir = os.path.join(os.path.dirname(current_dir), "models")
        else:
            self.model_dir = model_dir
            
        self.model_path = os.path.join(self.model_dir, "inventory_lgbm_model.pkl")
        self.meta_path = os.path.join(self.model_dir, "model_meta.joblib")
        # 'hist_4w_avg' 피처 추가 (과거 4주 동 요일 동 시간 평균)
        self.feature_cols = ['hour', 'weekday', 'is_weekend', 'lag_1h', 'lag_2h', 'rolling_mean_3h', 'store_avg', 'item_avg', 'hist_4w_avg']
        self.stats = {}
        self.load_model()

    def _prepare_training_data(self, history_df: pd.DataFrame, is_training: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        df = history_df.copy()
        
        # SALE_QTY 타입 보정
        df['SALE_QTY'] = pd.to_numeric(df['SALE_QTY'], errors='coerce').fillna(0)
        df['TMZON_DIV'] = pd.to_numeric(df['TMZON_DIV'], errors='coerce').fillna(0).astype(int)

        if is_training:
            # 학습 데이터 밸런싱 (판매가 0인 구간과 있는 구간의 비율 조정)
            zero_sales = df[df['SALE_QTY'] == 0]
            non_zero_sales = df[df['SALE_QTY'] > 0]
            sample_size = min(len(zero_sales), int(len(non_zero_sales) * 1.5))
            zero_sales_sampled = zero_sales.sample(n=sample_size, random_state=42) if not zero_sales.empty else zero_sales
            df = pd.concat([non_zero_sales, zero_sales_sampled]).sort_values(['SALE_DT', 'TMZON_DIV'])

        df['sale_dt_dt'] = pd.to_datetime(df['SALE_DT'], format='%Y%m%d')
        df['hour'] = df['TMZON_DIV']
        df['weekday'] = df['sale_dt_dt'].dt.weekday
        df['is_weekend'] = (df['weekday'] >= 5).astype(int)
        
        df = df.sort_values(['MASKED_STOR_CD', 'ITEM_CD', 'SALE_DT', 'hour'])
        
        # --- [초고도화] 역사적 중앙값 피처 계산 (이상치에 강한 Median 활용) ---
        # MAE를 낮추는 데는 평균(Mean)보다 중앙값(Median)이 훨씬 효과적입니다.
        hist_stats = df.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'weekday', 'hour'])['SALE_QTY'].median().reset_index()
        hist_stats.rename(columns={'SALE_QTY': 'hist_4w_avg'}, inplace=True)
        
        # 통계 정보 저장
        if is_training:
            self.stats['hist'] = hist_stats
            self.stats['store'] = df.groupby('MASKED_STOR_CD')['SALE_QTY'].median().to_dict()
            self.stats['item'] = df.groupby('ITEM_CD')['SALE_QTY'].median().to_dict()

        df = pd.merge(df, hist_stats, on=['MASKED_STOR_CD', 'ITEM_CD', 'weekday', 'hour'], how='left')
        
        group = df.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY']
        df['lag_1h'] = group.shift(1).fillna(0)
        df['lag_2h'] = group.shift(2).fillna(0)
        df['rolling_mean_3h'] = group.transform(lambda x: x.shift(1).rolling(3, min_periods=1).median()).fillna(0)
        
        df['store_avg'] = df['MASKED_STOR_CD'].map(self.stats.get('store', {})).fillna(0)
        df['item_avg'] = df['ITEM_CD'].map(self.stats.get('item', {})).fillna(0)
        
        if is_training:
            # 상위 5% 극단적 이상치 제거하여 학습 안정성 확보
            q_limit = df['SALE_QTY'].quantile(0.95)
            df = df[df['SALE_QTY'] <= q_limit]
            
        return df[self.feature_cols], df['SALE_QTY']

    def train(self, history_df: pd.DataFrame):
        X, y = self._prepare_training_data(history_df, is_training=True)
        if X.empty:
            logger.warning("학습할 데이터가 부족합니다.")
            return

        if HAS_LGB:
            # MAE 최적화 파라미터 (Objective: regression_l1)
            params = {
                'objective': 'regression_l1', 'metric': 'mae', 'verbosity': -1, 'boosting_type': 'gbdt',
                'learning_rate': 0.01, 'num_leaves': 31, 'max_depth': 8, 'min_child_samples': 20,
                'feature_fraction': 0.7, 'n_jobs': -1
            }
            train_data = lgb.Dataset(X, label=y)
            self.model = lgb.train(params, train_data, num_boost_round=2000)
        else:
            self.model = RandomForestRegressor(n_estimators=200, max_depth=10, n_jobs=-1)
            self.model.fit(X, y)
            
        self.save_model()
        logger.info("중앙값 기반 MAE 최소화 모델 학습 완료.")

    def save_model(self):
        if not os.path.exists(self.model_dir): os.makedirs(self.model_dir)
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.stats, self.meta_path)

    def load_model(self) -> bool:
        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
                if os.path.exists(self.meta_path):
                    self.stats = joblib.load(self.meta_path)
                return True
            except: pass
        return False

    def evaluate(self, test_df: pd.DataFrame) -> Dict[str, float]:
        """다양한 지표를 활용한 모델 성능 평가 (MAE, MAPE, RMSE, R2)"""
        if self.model is None:
            return {}
        X_test, y_test = self._prepare_training_data(test_df, is_training=False)
        if X_test.empty:
            return {}
        
        preds = self.model.predict(X_test)
        y_test_vals = y_test.values
        
        # 1. MAE
        mae = np.mean(np.abs(preds - y_test_vals))
        
        # 2. RMSE
        rmse = np.sqrt(np.mean((preds - y_test_vals)**2))
        
        # 3. MAPE (실제값이 0인 경우 제외하고 계산)
        mask = y_test_vals > 0
        if np.any(mask):
            mape = np.mean(np.abs((y_test_vals[mask] - preds[mask]) / y_test_vals[mask])) * 100
        else:
            mape = 0.0
            
        # 4. R2 Score
        ss_res = np.sum((y_test_vals - preds)**2)
        ss_tot = np.sum((y_test_vals - np.mean(y_test_vals))**2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        
        return {
            "MAE": float(mae),
            "MAPE": float(mape),
            "RMSE": float(rmse),
            "R2": float(r2)
        }

    def predict_next_hour_sales(self, store_cd: str, item_cd: str, current_time: datetime, history_df: pd.DataFrame, campaign_df: Optional[pd.DataFrame] = None) -> float:
        if self.model is None: return 0.0
            
        target_time = current_time + timedelta(hours=1)
        target_hour = target_time.hour
        weekday = target_time.weekday()
        target_date_str = target_time.strftime('%Y%m%d')
        
        # 1. 역사적 중앙값 추출 (동 요일, 동 시간) - 가장 믿을 수 있는 베이스라인
        hist_df = self.stats.get('hist', pd.DataFrame())
        hist_4w_median = 0.0
        if not hist_df.empty:
            match = hist_df[
                (hist_df['MASKED_STOR_CD'] == store_cd) & 
                (hist_df['ITEM_CD'] == item_cd) & 
                (hist_df['weekday'] == weekday) & 
                (hist_df['hour'] == target_hour)
            ]
            if not match.empty:
                hist_4w_median = float(match['hist_4w_avg'].iloc[0])

        # [프로모션 Uplift 로직 추가]
        promo_multiplier = 1.0
        if campaign_df is not None and not campaign_df.empty:
            # 현재 아이템이 오늘 날짜에 진행 중인 프로모션 목록 찾기
            active_promos = campaign_df[
                (campaign_df['item_cd'] == item_cd) & 
                (campaign_df['start_dt'] <= target_date_str) & 
                (campaign_df['fnsh_dt'] >= target_date_str)
            ]
            
            if not active_promos.empty:
                # 과거 유사 프로모션 기간의 평균 판매 상승률 계산 (간소화된 경험적 접근)
                # 실제 환경에서는 과거 해당 프로모션 기간의 판매량 / 비프로모션 기간 판매량을 집계하여 사용
                # 여기서는 할인율(dc_rate_amt) 또는 프로모션 종류에 따라 20% ~ 50% 상승률 차등 적용
                max_dc_rate = pd.to_numeric(active_promos['dc_rate_amt'], errors='coerce').max()
                if pd.notna(max_dc_rate) and max_dc_rate > 0:
                    if max_dc_rate <= 100: # 100% 미만은 할인율로 간주 (예: 20%)
                        promo_multiplier = 1.0 + (max_dc_rate / 100) * 1.5
                    else: # 100 이상의 금액 할인이면 기본 1.3배
                        promo_multiplier = 1.3
                else:
                    promo_multiplier = 1.4 # 기본 프로모션 상승률 40%

        # 2. 최근 실시간 판매 트렌드 추출
        recent_cutoff = current_time - timedelta(hours=3)
        df_recent = history_df[
            (history_df['MASKED_STOR_CD'] == store_cd) & 
            (history_df['ITEM_CD'] == item_cd)
        ].copy()
        
        if not df_recent.empty:
            df_recent['sale_datetime'] = pd.to_datetime(df_recent['SALE_DT'], format='%Y%m%d', errors='coerce') + \
                                         pd.to_timedelta(pd.to_numeric(df_recent['TMZON_DIV'], errors='coerce').fillna(0).astype(int), unit='h')
            
            recent = df_recent[
                (df_recent['sale_datetime'] > recent_cutoff) & 
                (df_recent['sale_datetime'] <= current_time)
            ].sort_values('sale_datetime')
        else:
            recent = pd.DataFrame()
        
        lag_1h = recent['SALE_QTY'].iloc[-1] if len(recent) >= 1 else 0
        lag_2h = recent['SALE_QTY'].iloc[-2] if len(recent) >= 2 else 0
        current_median_velocity = recent['SALE_QTY'].median() if not recent.empty else 0
        
        # 3. 모델 입력 및 예측
        X_pred = pd.DataFrame([[
            target_hour, weekday, 1 if weekday >= 5 else 0,
            lag_1h, lag_2h, current_median_velocity,
            self.stats.get('store', {}).get(store_cd, 0),
            self.stats.get('item', {}).get(item_cd, 0),
            hist_4w_median
        ]], columns=self.feature_cols)
        
        ml_pred = float(self.model.predict(X_pred)[0]) if self.model else 0.0
        
        # 4. [보정 로직 최종 고도화: Volume-Aware Anchor Strategy] 
        if hist_4w_median > 5:
            base_pred = (hist_4w_median * 0.95) + (ml_pred * 0.05)
            trend_ratio = np.clip(current_median_velocity / hist_4w_median if hist_4w_median > 0 else 1.0, 0.95, 1.05)
            final_pred = base_pred * trend_ratio
        else:
            base_pred = (hist_4w_median * 0.8) + (ml_pred * 0.2)
            trend_ratio = np.clip(current_median_velocity / hist_4w_median if hist_4w_median > 0 else 1.0, 0.8, 1.2)
            final_pred = base_pred * trend_ratio
            
        # [프로모션 Uplift 적용]
        if promo_multiplier > 1.0:
            # 프로모션 중일 경우 상승률을 곱하고, 최대치 제한(clip)을 완화함
            final_pred = final_pred * promo_multiplier
            if hist_4w_median > 0:
                final_pred = np.clip(final_pred, hist_4w_median * 0.7, hist_4w_median * promo_multiplier * 1.5)
        else:
            if hist_4w_median > 0:
                final_pred = np.clip(final_pred, hist_4w_median * 0.7, hist_4w_median * 1.3)
                
        final_pred = round(final_pred, 0)
        return float(max(0, final_pred))



# ==========================================
# 4. 생산 관리 통합 에이전트 (Main Agent)
# ==========================================
class ProductionManagementAgent:
    """
    [Production-Ready] 생산 관리 통합 에이전트
    - 재고 역산 엔진(InventoryReversalEngine) 연동
    - ML 기반 판매 예측(InventoryPredictor) 연동
    - 찬스로스 및 ROI 분석(ChanceLossService) 연동
    """
    def __init__(self, 
                 inventory_df: pd.DataFrame, 
                 production_df: pd.DataFrame, 
                 sales_df: pd.DataFrame, 
                 campaign_df: Optional[pd.DataFrame] = None,
                 production_list_df: Optional[pd.DataFrame] = None):
        
        self.engine = InventoryReversalEngine(inventory_df, production_df, sales_df)
        self.historical_sales_df = sales_df
        self.campaign_df = campaign_df if campaign_df is not None else pd.DataFrame()
        self.production_list_df = production_list_df if production_list_df is not None else pd.DataFrame()
        
        self.chance_loss_service = ChanceLossService(sales_df, self.campaign_df)
        self.predictor = InventoryPredictor()

    def calculate_sales_velocity(self, store_cd: str, item_cd: str, target_date: str, current_time: datetime) -> float:
        """평소 4주 평균 대비 오늘의 판매 속도(배수) 계산"""
        current_hour = current_time.hour
        
        # 1. 오늘 현재 시간까지의 누적 판매량
        today_sales = self.historical_sales_df[
            (self.historical_sales_df['MASKED_STOR_CD'] == store_cd) & 
            (self.historical_sales_df['ITEM_CD'] == item_cd) &
            (self.historical_sales_df['SALE_DT'] == target_date) &
            (self.historical_sales_df['TMZON_DIV'].astype(int) <= current_hour)
        ]['SALE_QTY'].sum()

        # 2. 과거 4주 동요일, 현재 시간까지의 평균 누적 판매량
        target_dt = datetime.strptime(target_date, '%Y%m%d')
        start_hist = target_dt - timedelta(weeks=4)
        target_weekday = target_dt.weekday()

        hist_data = self.historical_sales_df[
            (self.historical_sales_df['MASKED_STOR_CD'] == store_cd) &
            (self.historical_sales_df['ITEM_CD'] == item_cd)
        ].copy()
        
        if hist_data.empty: return 1.0

        hist_data['sale_dt_dt'] = pd.to_datetime(hist_data['SALE_DT'], format='%Y%m%d')
        hist_past = hist_data[
            (hist_data['sale_dt_dt'] >= start_hist) & 
            (hist_data['sale_dt_dt'] < target_dt) &
            (hist_data['sale_dt_dt'].dt.weekday == target_weekday) &
            (hist_data['TMZON_DIV'].astype(int) <= current_hour)
        ]
        
        if hist_past.empty: return 1.0
        
        # 4주치 일자별 누적합의 평균 계산
        avg_past_sales = hist_past.groupby('SALE_DT')['SALE_QTY'].sum().mean()
        
        if avg_past_sales <= 0: return 1.0
        
        # 오늘 판매량 / 4주 평균 판매량 = 판매 속도 배수 (예: 1.5배)
        velocity = float(today_sales / avg_past_sales)
        return round(velocity, 2)

    def extract_production_pattern(self, store_cd: str, item_cd: str, target_date: str) -> dict:
        """과거 4주간의 주력 1차, 2차 생산 시간 및 수량 패턴 분석"""
        target_dt = datetime.strptime(target_date, '%Y%m%d')
        start_hist = target_dt - timedelta(weeks=4)
        
        hist_prod = self.engine.production_df[
            (self.engine.production_df['MASKED_STOR_CD'] == store_cd) &
            (self.engine.production_df['ITEM_CD'] == item_cd)
        ].copy()
        
        if hist_prod.empty:
            return {"1st": None, "2nd": None}
            
        hist_prod['prod_dt_dt'] = pd.to_datetime(hist_prod['PROD_DT'], format='%Y%m%d')
        hist_4w = hist_prod[(hist_prod['prod_dt_dt'] >= start_hist) & (hist_prod['prod_dt_dt'] < target_dt)]
        
        if hist_4w.empty: return {"1st": None, "2nd": None}

        # 시간대(차수)별 평균 생산량 및 빈도 계산
        pattern = hist_4w.groupby('PROD_DGRE')['PROD_QTY'].agg(['mean', 'count']).reset_index()
        pattern = pattern.sort_values(by='count', ascending=False) # 자주 굽는 순으로 정렬
        
        result = {"1st": None, "2nd": None}
        
        # 차수(PROD_DGRE)를 시간(HH:MM)으로 변환하는 내부 함수
        def dgre_to_time(dgre):
            try:
                hour = 8 + (int(dgre) - 1) * 2
                return f"{hour:02d}:00"
            except:
                return "08:00"

        if len(pattern) > 0:
            result["1st"] = {"time": dgre_to_time(pattern.iloc[0]['PROD_DGRE']), "qty": int(pattern.iloc[0]['mean'])}
        if len(pattern) > 1:
            result["2nd"] = {"time": dgre_to_time(pattern.iloc[1]['PROD_DGRE']), "qty": int(pattern.iloc[1]['mean'])}
            
        return result

    def get_realtime_status(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """실시간 재고 상태 및 1시간/2시간 후 예측 정보 조회"""
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')
        
        try:
            stock_flow = self.engine.get_estimated_stock(store_cd, item_cd, target_date)
            current_idx = stock_flow.index.asof(now)
            current_stock = float(stock_flow.at[current_idx, 'estimated_stock']) if current_idx in stock_flow.index else 0.0
        except Exception as e:
            logger.error(f"Inventory calculation failed: {e}")
            current_stock = 0.0

        pred_1h = self.predictor.predict_next_hour_sales(store_cd, item_cd, now, self.historical_sales_df)
        pred_2h_sum = pred_1h + self.predictor.predict_next_hour_sales(store_cd, item_cd, now + timedelta(hours=1), self.historical_sales_df)

        return {
            "current_stock": round(current_stock, 1),
            "predicted_sales_1h": round(pred_1h, 1),
            "predicted_sales_2h_total": round(pred_2h_sum, 1)
        }

    def generate_recommendation(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """생산 필요 여부 판단 및 패턴+예측 혼합형 추천 수량 산출"""
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')
        status = self.get_realtime_status(store_cd, item_cd, item_nm, now)
        
        curr_stock = status["current_stock"]
        pred_2h = status["predicted_sales_2h_total"]
        
        # 1. 생산 필요 판단 (현재고 < 2시간 예상 수요)
        need_production = bool(curr_stock < pred_2h)
        
        # [NEW] 점포 생산 품목 여부 확인 (완제품 납품 품목은 생산 추천 제외)
        is_production_item = True
        if not self.production_list_df.empty:
            is_production_item = not self.production_list_df[
                (self.production_list_df['MASKED_STOR_CD'] == store_cd) & 
                (self.production_list_df['ITEM_CD'] == item_cd)
            ].empty
        
        if not is_production_item:
            need_production = False

        # 2. 추천 수량 산출 (패턴 + 예측 혼합 알고리즘)
        recommend_qty = 0
        reason_text = "현재고가 충분합니다."
        
        if need_production:
            # A. 순수 부족분 (ML 예측 기반)
            ml_deficit = max(0, pred_2h - curr_stock)
            
            # B. 과거 4주 생산 패턴 (역추적 및 기록 기반)
            pattern = self.extract_production_pattern(store_cd, item_cd, target_date)
            # 현재 시간과 가장 가까운 차수의 패턴 수량 가져오기
            pattern_qty = 0
            if pattern.get("1st"):
                pattern_qty = pattern["1st"]["qty"]
            
            # C. 최종 블렌딩 (부족분 70% + 평소 생산량 30%)
            # 만약 평소 생산 패턴이 전혀 없다면(신규 상품 등) ML 예측에만 의존
            if pattern_qty > 0:
                blended_qty = (ml_deficit * 0.7) + (pattern_qty * 0.3)
                recommend_qty = int(np.ceil(blended_qty))
                reason_text = f"평소 생산량({pattern_qty}개)과 향후 예상 수요({int(pred_2h)}개)를 종합 고려하여 {recommend_qty}개 생산을 추천합니다."
            else:
                recommend_qty = int(np.ceil(ml_deficit))
                reason_text = f"향후 2시간 예상 수요 {int(pred_2h)}개에 맞춰 {recommend_qty}개 생산을 추천합니다."

        risk_level = "SAFE"
        if curr_stock <= 0: risk_level = "CRITICAL"
        elif need_production: risk_level = "WARNING"
        
        if not is_production_item:
            reason_text = "점포 생산 제외 품목(완제품)입니다. 생산 권고를 수행하지 않습니다."

        # 수익성 계산
        unit_price = 1500
        margin_rate = 0.3
        expected_gain = int(recommend_qty * unit_price * margin_rate) if need_production else 0

        past_loss = self.chance_loss_service.calculate_chance_loss(store_cd, item_cd, target_date, self.historical_sales_df)
        past_qty = past_loss.get("total_chance_loss_qty", 0)

        return {
            "timestamp": now.isoformat(),
            "item_info": {"item_cd": item_cd, "item_nm": item_nm},
            "inventory": {
                "current_qty": curr_stock,
                "status": risk_level
            },
            "prediction": status,
            "recommendation": {
                "need_production": need_production,
                "recommend_qty": recommend_qty,
                "reason": reason_text,
                "expected_profit_gain": expected_gain,
                "past_chance_loss_qty": past_qty
            }
        }

    def get_sku_status(self, store_cd: str, item_cd: str, item_nm: str, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """대시보드 표출을 위한 개별 SKU의 종합 상태 정보 생성"""
        now = current_time if current_time else datetime.now()
        target_date = now.strftime('%Y%m%d')

        # 1. 기본 상태 및 수량 
        rec = self.generate_recommendation(store_cd, item_cd, item_nm, now)
        current_qty = rec['inventory']['current_qty']
        predict_1h_qty = rec['prediction']['predicted_sales_1h']
        
        # 2. 4주 평균 생산 패턴 (1차, 2차)
        pattern = self.extract_production_pattern(store_cd, item_cd, target_date)
        
        # 3. 완제품 판별 (버튼 비활성화용)
        can_produce = True
        if not self.production_list_df.empty:
            can_produce = not self.production_list_df[
                (self.production_list_df['MASKED_STOR_CD'] == store_cd) & 
                (self.production_list_df['ITEM_CD'] == item_cd)
            ].empty

        # 4. 판매 속도(배수) 및 알림 메시지, 태그 로직
        velocity = self.calculate_sales_velocity(store_cd, item_cd, target_date, now)
        tags = []
        alert_msg = "정상적인 판매 추이입니다."

        if not can_produce:
            tags.append("완제품")
            alert_msg = "본사 납품 완제품으로 매장 자체 생산이 불가능합니다."
        elif velocity >= 1.3:
            tags.append("속도↑")
            alert_msg = f"오늘 판매 속도가 평소 대비 {velocity}배 빠릅니다. 조기 품절 및 추가 생산 검토를 권장합니다."
        elif current_qty <= predict_1h_qty and current_qty > 0:
            tags.append("품절임박")
            alert_msg = f"1시간 내 재고 소진이 예상됩니다."

        # Risk Level 한글 매핑
        risk_map = {"CRITICAL": "위험", "WARNING": "주의", "SAFE": "안전"}
        status_kor = risk_map.get(rec['inventory']['status'], "안전")

        # 찬스로스 절감 효과 (과거 대비)
        past_loss = rec['recommendation']['past_chance_loss_qty']
        reduction_pct = 0
        if past_loss > 0 and rec['recommendation']['need_production']:
            # 임의 로직: 생산 권장 시 과거 손실의 80%를 방어한다고 가정
            reduction_pct = int((past_loss * 0.8 / past_loss) * 100) if past_loss else 0
            if status_kor != "위험" and reduction_pct == 100: reduction_pct = 15 # UI 표현을 위한 보정

        return {
            "item_cd": item_cd,
            "item_nm": item_nm,
            "status": status_kor,
            "current_qty": int(current_qty),
            "predict_1h_qty": int(predict_1h_qty),
            "avg_4w_prod_1st": pattern.get("1st"),
            "avg_4w_prod_2nd": pattern.get("2nd"),
            "chance_loss_reduction_pct": reduction_pct,
            "sales_velocity": velocity,
            "tags": tags,
            "alert_message": alert_msg,
            "can_produce": can_produce
        }
