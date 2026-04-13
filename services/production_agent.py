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
        """5분 단위 추정 재고 테이블 생성"""
        logger.info(f"Calculating stock flow for Store: {store_cd}, Item: {item_cd}, Date: {target_date}")

        base_stock_row = self.inventory_df[
            (self.inventory_df['MASKED_STOR_CD'] == store_cd) & 
            (self.inventory_df['ITEM_CD'] == item_cd) &
            (self.inventory_df['STOCK_DT'] == target_date)
        ]
        base_stock = base_stock_row['STOCK_QTY'].sum() if not base_stock_row.empty else 0

        prod_data = self.production_df[
            (self.production_df['MASKED_STOR_CD'] == store_cd) & 
            (self.production_df['ITEM_CD'] == item_cd) &
            (self.production_df['PROD_DT'] == target_date)
        ].copy()
        
        def map_prod_time(dgre):
            try:
                hour = 8 + (int(dgre) - 1) * 2
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=9)

        if not prod_data.empty:
            prod_data['timestamp'] = prod_data['PROD_DGRE'].apply(map_prod_time)

        sales_data = self.sales_df[
            (self.sales_df['MASKED_STOR_CD'] == store_cd) & 
            (self.sales_df['ITEM_CD'] == item_cd) &
            (self.sales_df['SALE_DT'] == target_date)
        ].copy()
        
        def map_sale_time(tmzon):
            try:
                hour = int(tmzon)
                return datetime.strptime(target_date, '%Y%m%d') + timedelta(hours=hour)
            except:
                return datetime.strptime(target_date, '%Y%m%d')

        if not sales_data.empty:
            sales_data['timestamp'] = sales_data['TMZON_DIV'].apply(map_sale_time)

        start_time = datetime.strptime(target_date, '%Y%m%d')
        end_time = start_time + timedelta(days=1)
        timeline = pd.date_range(start=start_time, end=end_time, freq='5min', inclusive='left')
        
        df_flow = pd.DataFrame(index=timeline)
        df_flow['in_qty'] = 0.0
        df_flow['out_qty'] = 0.0

        for _, row in prod_data.iterrows():
            ts = row['timestamp']
            if ts in df_flow.index:
                df_flow.at[ts, 'in_qty'] += row['PROD_QTY']

        for _, row in sales_data.iterrows():
            ts = row['timestamp']
            for i in range(12):
                slot = ts + timedelta(minutes=i*5)
                if slot in df_flow.index:
                    df_flow.at[slot, 'out_qty'] += (row['SALE_QTY'] / 12)

        df_flow['stock_change'] = df_flow['in_qty'] - df_flow['out_qty']
        df_flow['estimated_stock'] = base_stock + df_flow['stock_change'].cumsum()
        
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
        self.feature_cols = ['hour', 'weekday', 'is_weekend', 'lag_1h', 'lag_2h', 'rolling_mean_3h', 'store_avg', 'item_avg']
        self.stats = {}
        self.load_model()

    def _prepare_training_data(self, history_df: pd.DataFrame, is_training: bool = True) -> Tuple[pd.DataFrame, pd.Series]:
        df = history_df.copy()
        if is_training:
            zero_sales = df[df['SALE_QTY'] == 0]
            non_zero_sales = df[df['SALE_QTY'] > 0]
            sample_size = min(len(zero_sales), int(len(non_zero_sales) * 1.5))
            zero_sales_sampled = zero_sales.sample(n=sample_size, random_state=42)
            df = pd.concat([non_zero_sales, zero_sales_sampled]).sort_values(['SALE_DT', 'TMZON_DIV'])

        df['sale_dt_dt'] = pd.to_datetime(df['SALE_DT'], format='%Y%m%d')
        df['hour'] = df['TMZON_DIV'].astype(int)
        df['weekday'] = df['sale_dt_dt'].dt.weekday
        df['is_weekend'] = (df['weekday'] >= 5).astype(int)
        
        df = df.sort_values(['MASKED_STOR_CD', 'ITEM_CD', 'SALE_DT', 'hour'])
        group = df.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY']
        
        df['lag_1h'] = group.shift(1).fillna(0)
        df['lag_2h'] = group.shift(2).fillna(0)
        df['rolling_mean_3h'] = group.transform(lambda x: x.shift(1).rolling(3, min_periods=1).mean()).fillna(0)
        
        if is_training:
            q_limit = df['SALE_QTY'].quantile(0.99)
            df = df[df['SALE_QTY'] <= q_limit]
            self.stats['store'] = df.groupby('MASKED_STOR_CD')['SALE_QTY'].mean().to_dict()
            self.stats['item'] = df.groupby('ITEM_CD')['SALE_QTY'].mean().to_dict()
            
        df['store_avg'] = df['MASKED_STOR_CD'].map(self.stats.get('store', {})).fillna(0)
        df['item_avg'] = df['ITEM_CD'].map(self.stats.get('item', {})).fillna(0)
        return df[self.feature_cols], df['SALE_QTY']

    def train(self, history_df: pd.DataFrame):
        X, y = self._prepare_training_data(history_df, is_training=True)
        if HAS_LGB:
            sample_weight = np.log1p(y) + 1.0
            params = {
                'objective': 'regression', 'metric': 'mae', 'verbosity': -1, 'boosting_type': 'gbdt',
                'learning_rate': 0.05, 'num_leaves': 63, 'max_depth': -1, 'min_child_samples': 10,
                'feature_fraction': 0.8, 'lambda_l1': 0.05, 'n_jobs': -1
            }
            train_data = lgb.Dataset(X, label=y, weight=sample_weight)
            self.model = lgb.train(params, train_data, num_boost_round=500)
        else:
            self.model = RandomForestRegressor(n_estimators=100)
            self.model.fit(X, y)
        self.save_model()
        logger.info("데이터 밸런싱이 적용된 최적화 모델 재학습 완료.")

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

    def predict_next_hour_sales(self, store_cd: str, item_cd: str, current_time: datetime, history_df: pd.DataFrame) -> float:
        if self.model is None: return 3.0
            
        target_time = current_time + timedelta(hours=1)
        weekday = target_time.weekday()
        
        recent = history_df[
            (history_df['MASKED_STOR_CD'] == store_cd) & 
            (history_df['ITEM_CD'] == item_cd)
        ].sort_values(['SALE_DT', 'TMZON_DIV']).tail(3)
        
        lag_1h = recent['SALE_QTY'].iloc[-1] if len(recent) >= 1 else 0
        lag_2h = recent['SALE_QTY'].iloc[-2] if len(recent) >= 2 else 0
        current_velocity = recent['SALE_QTY'].mean() if not recent.empty else 0
        
        X_pred = pd.DataFrame([[
            target_time.hour, weekday, 1 if weekday >= 5 else 0,
            lag_1h, lag_2h, current_velocity,
            self.stats.get('store', {}).get(store_cd, 0),
            self.stats.get('item', {}).get(item_cd, 0)
        ]], columns=self.feature_cols)
        
        ml_pred = float(self.model.predict(X_pred)[0])
        final_pred = (ml_pred * 0.7) + (current_velocity * 0.3)
        item_avg = self.stats.get('item', {}).get(item_cd, 1.0)
        final_pred = max(final_pred, item_avg * 0.5) 
        
        return max(0.0, round(final_pred, 2))


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
        """생산 필요 여부 판단 및 추천 수량 산출"""
        now = current_time if current_time else datetime.now()
        status = self.get_realtime_status(store_cd, item_cd, item_nm, now)
        
        curr_stock = status["current_stock"]
        pred_2h = status["predicted_sales_2h_total"]
        
        need_production = bool(curr_stock < pred_2h)
        
        is_production_item = True
        if not self.production_list_df.empty:
            is_production_item = not self.production_list_df[
                (self.production_list_df['MASKED_STOR_CD'] == store_cd) & 
                (self.production_list_df['ITEM_CD'] == item_cd)
            ].empty
        
        if not is_production_item:
            need_production = False
            logger.info(f"Item {item_cd} is a finished good for store {store_cd}. Skipping production recommendation.")

        risk_level = "SAFE"
        if curr_stock <= 0: risk_level = "CRITICAL"
        elif need_production: risk_level = "WARNING"

        recommend_qty = 0
        if need_production:
            deficit = pred_2h - curr_stock
            recommend_qty = int(np.ceil(max(1, deficit)))

        unit_price = 1500
        margin_rate = 0.3
        expected_gain = int(pred_2h * unit_price * margin_rate) if need_production else 0

        past_loss = self.chance_loss_service.calculate_chance_loss(store_cd, item_cd, now.strftime('%Y%m%d'), self.historical_sales_df)
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
