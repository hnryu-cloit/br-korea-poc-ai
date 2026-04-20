import os
import sys
import logging
import pandas as pd
import numpy as np
import time
from sqlalchemy import create_engine, text

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("data_mart_prep_advanced")

def load_and_clean_raw_data(engine) -> tuple[pd.DataFrame, pd.DataFrame]:
    """DB에서 원본 데이터를 불러와 기본 데이터 타입 및 컬럼명을 정제합니다."""
    logger.info("1. DB 원본 데이터 로딩 시작...")
    sales_query = text('SELECT masked_stor_cd, item_cd, sale_dt, tmzon_div, sale_qty FROM raw_daily_store_item_tmzon')
    campaign_query = text('''
        SELECT DISTINCT i.item_cd, REPLACE(m.start_dt, '-', '') as start_dt, REPLACE(m.fnsh_dt, '-', '') as fnsh_dt
        FROM raw_campaign_master m 
        JOIN raw_campaign_item i ON m.cmp_cd = i.cmp_cd
    ''')
    
    df_sales = pd.read_sql(sales_query, engine)
    df_camp = pd.read_sql(campaign_query, engine)
    
    df_sales.columns = [c.upper() for c in df_sales.columns]
    df_camp.columns = [c.upper() for c in df_camp.columns]
    
    df_sales['SALE_QTY_FLOAT'] = pd.to_numeric(df_sales['SALE_QTY'], errors='coerce').fillna(0).astype(float)
    return df_sales, df_camp

def estimate_and_filter_op_hours(df_sales: pd.DataFrame, engine) -> pd.DataFrame:
    """매출 발생 패턴을 기반으로 매장별 영업시간을 추정, DB에 저장 후 해당 시간대만 필터링합니다."""
    logger.info("2. 매장별 판매 패턴 기반 영업시간 추정 및 필터링 중...")
    active_sales = df_sales[df_sales['SALE_QTY_FLOAT'] > 0].copy()
    active_sales['HOUR_INT'] = active_sales['TMZON_DIV'].astype(int)
    
    daily_limits = active_sales.groupby(['MASKED_STOR_CD', 'SALE_DT'])['HOUR_INT'].agg(['min', 'max']).reset_index()
    store_op_hours = daily_limits.groupby('MASKED_STOR_CD').agg({
        'min': lambda x: int(np.percentile(x, 10)),
        'max': lambda x: int(np.percentile(x, 90))
    }).reset_index()
    store_op_hours.columns = ['MASKED_STOR_CD', 'OPEN_HOUR', 'CLOSE_HOUR']
    
    store_op_hours.to_sql('store_operating_hours', engine, if_exists='replace', index=False)
    
    df_sales['SALE_QTY'] = df_sales['SALE_QTY_FLOAT'].astype(np.float32)
    df_sales.drop(columns=['SALE_QTY_FLOAT'], inplace=True)
    df_sales['DATETIME'] = pd.to_datetime(df_sales['SALE_DT'] + df_sales['TMZON_DIV'].astype(str).str.zfill(2), format='%Y%m%d%H')
    df_sales['HOUR'] = df_sales['DATETIME'].dt.hour.astype(np.int8)
    df_sales['WEEKDAY'] = df_sales['DATETIME'].dt.weekday.astype(np.int8)
    df_sales['IS_WEEKEND'] = (df_sales['WEEKDAY'] >= 5).astype(np.int8)
    
    df_sales = df_sales.merge(store_op_hours, on='MASKED_STOR_CD', how='inner')
    df_sales = df_sales[(df_sales['HOUR'] >= df_sales['OPEN_HOUR']) & (df_sales['HOUR'] <= df_sales['CLOSE_HOUR'])].copy()
    df_sales.drop(['OPEN_HOUR', 'CLOSE_HOUR'], axis=1, inplace=True)
    return df_sales

def map_campaign_events(df_sales: pd.DataFrame, df_camp: pd.DataFrame) -> pd.DataFrame:
    """상품별 캠페인(이벤트) 진행 여부를 맵핑합니다."""
    logger.info("3. 이벤트 플래그 매핑 중...")
    df_merge = df_sales[['ITEM_CD', 'SALE_DT']].drop_duplicates().merge(df_camp, on='ITEM_CD')
    df_event = df_merge[(df_merge['SALE_DT'] >= df_merge['START_DT']) & (df_merge['SALE_DT'] <= df_merge['FNSH_DT'])]
    df_event_map = df_event[['ITEM_CD', 'SALE_DT']].drop_duplicates()
    df_event_map['IS_EVENT'] = 1
    
    df_sales = df_sales.merge(df_event_map, on=['ITEM_CD', 'SALE_DT'], how='left')
    df_sales['IS_EVENT'] = df_sales['IS_EVENT'].fillna(0).astype(np.int8)
    return df_sales

def join_store_clusters(df_sales: pd.DataFrame, engine) -> pd.DataFrame:
    """DB에서 사전 계산된 매장 클러스터 정보를 가져와 병합합니다."""
    logger.info("4. DB에서 매장 클러스터 정보 로드 및 병합 중...")
    try:
        store_clusters = pd.read_sql("SELECT masked_stor_cd, cluster_id as STORE_CLUSTER FROM store_clusters", engine)
        store_clusters.columns = [c.upper() for c in store_clusters.columns]
        return df_sales.merge(store_clusters, on='MASKED_STOR_CD', how='left')
    except Exception as e:
        logger.warning(f"⚠️ 클러스터 테이블을 찾을 수 없습니다. cluster_stores.py를 먼저 실행하세요. (Error: {e})")
        df_sales['STORE_CLUSTER'] = 0 # 기본값 할당
        return df_sales

def calc_historical_pure_average(df_sales: pd.DataFrame) -> pd.DataFrame:
    """행사 기간을 제외한 순수 과거 4주 판매 평균(HIST_4W_AVG)을 산출합니다."""
    logger.info("5. 과거 4주 (순수 일반 판매) 평균(HIST_4W_AVG) 계산 중...")
    df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR', 'DATETIME'])
    g_sales = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['SALE_QTY']
    g_event = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['IS_EVENT']
    
    s1, s2, s3, s4 = g_sales.shift(1), g_sales.shift(2), g_sales.shift(3), g_sales.shift(4)
    e1, e2, e3, e4 = g_event.shift(1), g_event.shift(2), g_event.shift(3), g_event.shift(4)
    
    n1, n2, n3, n4 = s1.where(e1 == 0), s2.where(e2 == 0), s3.where(e3 == 0), s4.where(e4 == 0)
    
    df_sales['HIST_4W_AVG'] = pd.concat([n1, n2, n3, n4], axis=1).mean(axis=1, skipna=True)
    df_sales['HIST_4W_AVG'] = df_sales['HIST_4W_AVG'].fillna(0).astype(np.float32)
    return df_sales

def apply_cold_start_transition(df_sales: pd.DataFrame) -> pd.DataFrame:
    """신제품/신규지점의 경우 클러스터 참조 패턴에서 자체 데이터로 점진적 가중치 전환을 수행합니다."""
    logger.info("6. 신제품 클러스터 참조 및 점진적 가중치 전환(Soft Transition) 적용 중...")
    store_item_intro = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['DATETIME'].min().rename('INTRO_DT')
    df_sales = df_sales.merge(store_item_intro.reset_index(), on=['MASKED_STOR_CD', 'ITEM_CD'], how='left')
    df_sales['DAYS_SINCE_INTRO'] = (df_sales['DATETIME'] - df_sales['INTRO_DT']).dt.days
    
    cluster_pattern = df_sales.groupby(['STORE_CLUSTER', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['SALE_QTY'].mean().rename('CLUSTER_REF_AVG')
    df_sales = df_sales.merge(cluster_pattern.reset_index(), on=['STORE_CLUSTER', 'ITEM_CD', 'WEEKDAY', 'HOUR'], how='left')
    
    df_sales['OWN_WEIGHT'] = np.clip((df_sales['DAYS_SINCE_INTRO'] - 14) / 14.0, 0.0, 1.0)
    
    mask_need_ref = (df_sales['DAYS_SINCE_INTRO'] <= 28) | (df_sales['HIST_4W_AVG'] == 0)
    df_sales.loc[mask_need_ref, 'HIST_4W_AVG'] = (
        df_sales.loc[mask_need_ref, 'HIST_4W_AVG'] * df_sales.loc[mask_need_ref, 'OWN_WEIGHT'] +
        df_sales.loc[mask_need_ref, 'CLUSTER_REF_AVG'].fillna(0) * (1 - df_sales.loc[mask_need_ref, 'OWN_WEIGHT'])
    )
    
    menu_hourly_avg = df_sales.groupby(['DATETIME'])['SALE_QTY'].transform('mean').astype(np.float32)
    df_sales['HIST_4W_AVG'] = df_sales['HIST_4W_AVG'].replace(0, np.nan).fillna(menu_hourly_avg).fillna(0)
    df_sales.drop(['INTRO_DT', 'DAYS_SINCE_INTRO', 'CLUSTER_REF_AVG', 'OWN_WEIGHT'], axis=1, inplace=True)
    return df_sales

def correct_out_of_stock(df_sales: pd.DataFrame) -> pd.DataFrame:
    """연속 3시간 매출 0일 경우 재고 소진(OOS)으로 간주하고 과거 평균으로 판매량을 보정합니다."""
    logger.info("7. 재고 소진(OOS) 탐지 및 보정 중...")
    df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'DATETIME'])
    df_sales['IS_ZERO'] = (df_sales['SALE_QTY'] == 0).astype(int)
    group_changed = (df_sales['MASKED_STOR_CD'] != df_sales['MASKED_STOR_CD'].shift()) | (df_sales['ITEM_CD'] != df_sales['ITEM_CD'].shift()) | (df_sales['IS_ZERO'] != df_sales['IS_ZERO'].shift())
    df_sales['ZERO_BLOCK_ID'] = group_changed.cumsum()
    block_counts = df_sales.groupby('ZERO_BLOCK_ID')['IS_ZERO'].transform('sum')
    
    oos_mask = (df_sales['IS_ZERO'] == 1) & (block_counts >= 3)
    df_sales.loc[oos_mask, 'SALE_QTY'] = df_sales.loc[oos_mask, 'HIST_4W_AVG']
    df_sales.drop(['IS_ZERO', 'ZERO_BLOCK_ID'], axis=1, inplace=True)
    return df_sales

def remove_outliers_and_inactive(df_sales: pd.DataFrame) -> pd.DataFrame:
    """대형 예약 주문 방어를 위한 상한선 Clipping 및 최근 미판매 제품 필터링."""
    logger.info("8. 예약 주문(특납) 제외 및 이상치 보정 중...")
    last_30d_start = df_sales['DATETIME'].max() - pd.Timedelta(days=30)
    active_combos = df_sales[df_sales['DATETIME'] >= last_30d_start].groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY'].sum()
    active_combos = active_combos[active_combos > 0].reset_index()[['MASKED_STOR_CD', 'ITEM_CD']]
    df_sales = df_sales.merge(active_combos, on=['MASKED_STOR_CD', 'ITEM_CD'], how='inner')
    
    normal_sales = df_sales[df_sales['IS_EVENT'] == 0]
    item_thresholds = normal_sales.groupby('ITEM_CD')['SALE_QTY'].quantile(0.99).astype(np.float32).rename('NORMAL_UPPER_BOUND')
    df_sales = df_sales.merge(item_thresholds, on='ITEM_CD', how='left')
    df_sales['NORMAL_UPPER_BOUND'] = df_sales['NORMAL_UPPER_BOUND'].fillna(df_sales['SALE_QTY'].quantile(0.99))
    
    outlier_mask = (df_sales['IS_EVENT'] == 0) & (df_sales['SALE_QTY'] > df_sales['NORMAL_UPPER_BOUND'])
    df_sales.loc[outlier_mask, 'SALE_QTY'] = df_sales.loc[outlier_mask, 'NORMAL_UPPER_BOUND']
    df_sales.drop(['NORMAL_UPPER_BOUND'], axis=1, inplace=True)
    return df_sales

def generate_target_and_save(df_sales: pd.DataFrame, engine) -> None:
    """1시간 후 판매량 타겟 변수를 생성하고 완성된 마트를 DB에 저장합니다."""
    logger.info("9. TARGET 변수(1시간 후 판매량) 생성 중...")
    df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'DATETIME'])
    df_sales['TARGET_1H_AHEAD'] = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY'].shift(-1)
    df_sales = df_sales.dropna(subset=['TARGET_1H_AHEAD'])
    
    logger.info("10. Data Mart 테이블(ai_sales_data_mart)에 저장 중...")
    df_sales.to_sql('ai_sales_data_mart', engine, if_exists='replace', index=False, chunksize=50000, method='multi')

def prepare_and_save_mart(db_url: str):
    start_t = time.time()
    engine = create_engine(db_url)
    
    df_sales, df_camp = load_and_clean_raw_data(engine)
    df_sales = estimate_and_filter_op_hours(df_sales, engine)
    df_sales = map_campaign_events(df_sales, df_camp)
    
    # [수정] 자체 계산 대신 DB에서 클러스터 정보 로드
    df_sales = join_store_clusters(df_sales, engine)
    
    df_sales = calc_historical_pure_average(df_sales)
    df_sales = apply_cold_start_transition(df_sales)
    df_sales = correct_out_of_stock(df_sales)
    df_sales = remove_outliers_and_inactive(df_sales)
    generate_target_and_save(df_sales, engine)
    
    logger.info(f"✅ Data Mart 테이블 파이프라인 전체 완료! (총 소요시간: {time.time()-start_t:.1f}초)")

if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    prepare_and_save_mart(db_url)
