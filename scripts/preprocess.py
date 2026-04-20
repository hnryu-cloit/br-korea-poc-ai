import os
import sys
import logging
import pandas as pd
import numpy as np
import time
from sqlalchemy import create_engine, text
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("data_mart_prep_advanced")

def prepare_and_save_mart(db_url: str):
    engine = create_engine(db_url)
    start_t = time.time()
    
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
    
    df_sales['SALE_QTY'] = pd.to_numeric(df_sales['SALE_QTY'], errors='coerce').fillna(0).astype(np.float32)
    df_sales['DATETIME'] = pd.to_datetime(df_sales['SALE_DT'] + df_sales['TMZON_DIV'].astype(str).str.zfill(2), format='%Y%m%d%H')
    df_sales['HOUR'] = df_sales['DATETIME'].dt.hour.astype(np.int8)
    df_sales['WEEKDAY'] = df_sales['DATETIME'].dt.weekday.astype(np.int8)
    df_sales['IS_WEEKEND'] = (df_sales['WEEKDAY'] >= 5).astype(np.int8)
    
    logger.info("2. 매장별 영업시간 필터링 중...")
    op_hours_query = "SELECT masked_stor_cd, open_hour, close_hour FROM store_operating_hours"
    df_op_hours = pd.read_sql(op_hours_query, engine)
    df_op_hours.columns = [c.upper() for c in df_op_hours.columns]
    
    df_sales = df_sales.merge(df_op_hours, on='MASKED_STOR_CD', how='inner')
    df_sales = df_sales[(df_sales['HOUR'] >= df_sales['OPEN_HOUR']) & (df_sales['HOUR'] <= df_sales['CLOSE_HOUR'])].copy()
    df_sales.drop(['OPEN_HOUR', 'CLOSE_HOUR'], axis=1, inplace=True)
    
    logger.info("3. 이벤트 플래그 매핑 중...")
    df_merge = df_sales[['ITEM_CD', 'SALE_DT']].drop_duplicates().merge(df_camp, on='ITEM_CD')
    df_event = df_merge[(df_merge['SALE_DT'] >= df_merge['START_DT']) & (df_merge['SALE_DT'] <= df_merge['FNSH_DT'])]
    df_event_map = df_event[['ITEM_CD', 'SALE_DT']].drop_duplicates()
    df_event_map['IS_EVENT'] = 1
    df_sales = df_sales.merge(df_event_map, on=['ITEM_CD', 'SALE_DT'], how='left')
    df_sales['IS_EVENT'] = df_sales['IS_EVENT'].fillna(0).astype(np.int8)

    # ---------------------------------------------------------
    # [NEW] 4. 매장 행동 기반 클러스터링 (QA 5번 반영)
    # ---------------------------------------------------------
    logger.info("4. 매장 운영 패턴 기반 자동 클러스터링 중...")
    store_stats = df_sales.groupby('MASKED_STOR_CD').agg({
        'SALE_QTY': 'mean',
        'IS_WEEKEND': 'mean',
        'HOUR': lambda x: x.value_counts().index[0] if not x.empty else 12
    }).rename(columns={'SALE_QTY': 'AVG_QTY', 'IS_WEEKEND': 'WEEKEND_RATIO', 'HOUR': 'PEAK_HOUR'})
    
    scaler = StandardScaler()
    store_stats_scaled = scaler.fit_transform(store_stats)
    
    kmeans = KMeans(n_clusters=4, random_state=42, n_init=10)
    store_stats['STORE_CLUSTER'] = kmeans.fit_predict(store_stats_scaled)
    
    df_sales = df_sales.merge(store_stats[['STORE_CLUSTER']], on='MASKED_STOR_CD', how='left')

    # ---------------------------------------------------------
    # 5. 과거 4주 (순수 일반 판매) 평균 계산 (QA 1번 반영)
    # ---------------------------------------------------------
    logger.info("5. 과거 4주 (순수 일반 판매) 평균(HIST_4W_AVG) 계산 중...")
    df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR', 'DATETIME'])
    g_sales = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['SALE_QTY']
    g_event = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['IS_EVENT']
    
    s1, s2, s3, s4 = g_sales.shift(1), g_sales.shift(2), g_sales.shift(3), g_sales.shift(4)
    e1, e2, e3, e4 = g_event.shift(1), g_event.shift(2), g_event.shift(3), g_event.shift(4)
    
    n1 = s1.where(e1 == 0)
    n2 = s2.where(e2 == 0)
    n3 = s3.where(e3 == 0)
    n4 = s4.where(e4 == 0)
    
    df_sales['HIST_4W_AVG'] = pd.concat([n1, n2, n3, n4], axis=1).mean(axis=1, skipna=True)
    df_sales['HIST_4W_AVG'] = df_sales['HIST_4W_AVG'].fillna(0).astype(np.float32)

    # ---------------------------------------------------------
    # [NEW] 6. 신제품/신규지점 보정 및 점진적 가중치 전환 (QA 4, 5번 연동)
    # ---------------------------------------------------------
    logger.info("6. 신제품 클러스터 참조 및 점진적 가중치 전환(Soft Transition) 적용 중...")
    
    # 지점별 상품 도입일 추출
    store_item_intro = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['DATETIME'].min().rename('INTRO_DT')
    df_sales = df_sales.merge(store_item_intro.reset_index(), on=['MASKED_STOR_CD', 'ITEM_CD'], how='left')
    df_sales['DAYS_SINCE_INTRO'] = (df_sales['DATETIME'] - df_sales['INTRO_DT']).dt.days
    
    # 클러스터별/상품별/요일별/시간대별 표준 패턴 계산
    cluster_pattern = df_sales.groupby(['STORE_CLUSTER', 'ITEM_CD', 'WEEKDAY', 'HOUR'])['SALE_QTY'].mean().rename('CLUSTER_REF_AVG')
    df_sales = df_sales.merge(cluster_pattern.reset_index(), on=['STORE_CLUSTER', 'ITEM_CD', 'WEEKDAY', 'HOUR'], how='left')
    
    # 가중치 계산 (0~14일: 클러스터 100% / 15~28일: 점진적 전환 / 29일 이후: 자기데이터 100%)
    # OWN_WEIGHT: 자기 데이터의 반영 비중
    df_sales['OWN_WEIGHT'] = np.clip((df_sales['DAYS_SINCE_INTRO'] - 14) / 14.0, 0.0, 1.0)
    
    # 최종 HIST_4W_AVG 보정 (데이터 부족 시 클러스터 참조값과 블렌딩)
    mask_need_ref = (df_sales['DAYS_SINCE_INTRO'] <= 28) | (df_sales['HIST_4W_AVG'] == 0)
    df_sales.loc[mask_need_ref, 'HIST_4W_AVG'] = (
        df_sales.loc[mask_need_ref, 'HIST_4W_AVG'] * df_sales.loc[mask_need_ref, 'OWN_WEIGHT'] +
        df_sales.loc[mask_need_ref, 'CLUSTER_REF_AVG'].fillna(0) * (1 - df_sales.loc[mask_need_ref, 'OWN_WEIGHT'])
    )
    
    # 잔여 결측치 처리 (전체 시간대 평균)
    menu_hourly_avg = df_sales.groupby(['DATETIME'])['SALE_QTY'].transform('mean').astype(np.float32)
    df_sales['HIST_4W_AVG'] = df_sales['HIST_4W_AVG'].replace(0, np.nan).fillna(menu_hourly_avg).fillna(0)
    
    df_sales.drop(['INTRO_DT', 'DAYS_SINCE_INTRO', 'CLUSTER_REF_AVG', 'OWN_WEIGHT'], axis=1, inplace=True)

    # ---------------------------------------------------------
    # 7. 재고 소진(OOS) 보정
    # ---------------------------------------------------------
    logger.info("7. 재고 소진(OOS) 탐지 및 보정 중...")
    df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'DATETIME'])
    df_sales['IS_ZERO'] = (df_sales['SALE_QTY'] == 0).astype(int)
    group_changed = (df_sales['MASKED_STOR_CD'] != df_sales['MASKED_STOR_CD'].shift()) | (df_sales['ITEM_CD'] != df_sales['ITEM_CD'].shift()) | (df_sales['IS_ZERO'] != df_sales['IS_ZERO'].shift())
    df_sales['ZERO_BLOCK_ID'] = group_changed.cumsum()
    block_counts = df_sales.groupby('ZERO_BLOCK_ID')['IS_ZERO'].transform('sum')
    oos_mask = (df_sales['IS_ZERO'] == 1) & (block_counts >= 3)
    df_sales.loc[oos_mask, 'SALE_QTY'] = df_sales.loc[oos_mask, 'HIST_4W_AVG']
    df_sales.drop(['IS_ZERO', 'ZERO_BLOCK_ID'], axis=1, inplace=True)
    
    # ---------------------------------------------------------
    # 8. 예약 주문(특납) 제외 및 이상치 보정 (QA 2번 반영)
    # ---------------------------------------------------------
    logger.info("8. 예약 주문(특납) 제외 및 이상치 보정 중...")
    last_30d_start = df_sales['DATETIME'].max() - pd.Timedelta(days=30)
    active_combos = df_sales[df_sales['DATETIME'] >= last_30d_start].groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY'].sum()
    active_combos = active_combos[active_combos > 0].reset_index()[['MASKED_STOR_CD', 'ITEM_CD']]
    df_sales = df_sales.merge(active_combos, on=['MASKED_STOR_CD', 'ITEM_CD'], how='inner')
    
    # 상품별 일반적인 판매량 상한선(99%)을 계산하여 대형 예약 주문(특납)의 영향을 차단
    normal_sales = df_sales[df_sales['IS_EVENT'] == 0]
    item_thresholds = normal_sales.groupby('ITEM_CD')['SALE_QTY'].quantile(0.99).astype(np.float32).rename('NORMAL_UPPER_BOUND')
    df_sales = df_sales.merge(item_thresholds, on='ITEM_CD', how='left')
    df_sales['NORMAL_UPPER_BOUND'] = df_sales['NORMAL_UPPER_BOUND'].fillna(df_sales['SALE_QTY'].quantile(0.99))
    
    # 상한선을 초과하는 값(예약 주문 등)은 상한선으로 Clipping 하여 학습 데이터 왜곡 방지
    outlier_mask = (df_sales['IS_EVENT'] == 0) & (df_sales['SALE_QTY'] > df_sales['NORMAL_UPPER_BOUND'])
    df_sales.loc[outlier_mask, 'SALE_QTY'] = df_sales.loc[outlier_mask, 'NORMAL_UPPER_BOUND']
    df_sales.drop(['NORMAL_UPPER_BOUND'], axis=1, inplace=True)
    
    logger.info("9. TARGET 변수(1시간 후 판매량) 생성 중...")
    df_sales = df_sales.sort_values(by=['MASKED_STOR_CD', 'ITEM_CD', 'DATETIME'])
    df_sales['TARGET_1H_AHEAD'] = df_sales.groupby(['MASKED_STOR_CD', 'ITEM_CD'])['SALE_QTY'].shift(-1)
    df_sales = df_sales.dropna(subset=['TARGET_1H_AHEAD'])
    
    logger.info("10. Data Mart 테이블(ai_sales_data_mart)에 저장 중...")
    df_sales.to_sql('ai_sales_data_mart', engine, if_exists='replace', index=False, chunksize=50000, method='multi')
    
    logger.info(f"✅ Data Mart 테이블 생성 완료! (총 소요시간: {time.time()-start_t:.1f}초)")

if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    prepare_and_save_mart(db_url)
