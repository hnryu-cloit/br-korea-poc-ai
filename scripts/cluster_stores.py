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
logger = logging.getLogger("store_clustering")

def run_store_clustering(db_url: str, n_clusters: int = 4):
    """
    매장별 판매 데이터를 다각도로 분석하여 상권/운영 패턴 클러스터를 생성하고 DB에 저장합니다.
    """
    start_t = time.time()
    engine = create_engine(db_url)
    
    logger.info("1. 클러스터링을 위한 과거 판매 데이터 로드 중...")
    # 1-1. 시간대별 상품 판매 데이터 (규모, 시간대, 주말 피처용)
    query_sales = """
        SELECT masked_stor_cd, sale_dt, tmzon_div as hour, sale_qty 
        FROM raw_daily_store_item_tmzon
    """
    df = pd.read_sql(query_sales, engine)
    df['sale_qty'] = pd.to_numeric(df['sale_qty'], errors='coerce').fillna(0).astype(float)
    df['sale_dt'] = pd.to_datetime(df['sale_dt'], format='%Y%m%d')
    df['weekday'] = df['sale_dt'].dt.weekday
    df['is_weekend'] = (df['weekday'] >= 5).astype(int)
    
    # 1-2. 채널별(온/오프라인) 판매 데이터 (온라인 비중 피처용)
    # 가정: ORD_TYPE이 특정 코드이거나 테이블 이름에 기반하여 전체 매출 대비 온라인 채널 매출 집계
    query_online = """
        SELECT masked_stor_cd, sale_qty as online_qty
        FROM raw_daily_store_online
    """
    try:
        df_online = pd.read_sql(query_online, engine)
        df_online['online_qty'] = pd.to_numeric(df_online['online_qty'], errors='coerce').fillna(0).astype(float)
        store_online_sum = df_online.groupby('masked_stor_cd')['online_qty'].sum().reset_index()
    except Exception as e:
        logger.warning(f"온라인 채널 데이터를 불러오는 데 실패했습니다. 온라인 비중을 0으로 처리합니다. (Error: {e})")
        store_online_sum = pd.DataFrame(columns=['masked_stor_cd', 'online_qty'])
    
    logger.info("2. 매장별 행동 피처(Behavioral Features) 추출 중...")
    # 피처 1~4 추출
    store_features = df.groupby('masked_stor_cd').agg({
        'sale_qty': ['mean', 'std', 'sum'],
        'is_weekend': 'mean',
        'hour': lambda x: x.value_counts().index[0] if not x.empty else 12
    })
    
    store_features.columns = ['avg_qty', 'std_qty', 'total_qty', 'weekend_ratio', 'peak_hour']
    store_features = store_features.fillna(0).reset_index()
    
    # 피처 5: 온라인(배달/픽업) 비중 추가
    store_features = store_features.merge(store_online_sum, on='masked_stor_cd', how='left')
    store_features['online_qty'] = store_features['online_qty'].fillna(0)
    
    # 총 매출 중 온라인 매출이 차지하는 비율 계산 (0 분모 방지)
    store_features['online_ratio'] = np.where(
        store_features['total_qty'] > 0, 
        store_features['online_qty'] / store_features['total_qty'], 
        0
    )
    
    logger.info("3. K-Means 클러스터링 수행 중...")
    feature_cols = ['avg_qty', 'std_qty', 'weekend_ratio', 'peak_hour', 'online_ratio']
    scaler = StandardScaler()
    scaled_features = scaler.fit_transform(store_features[feature_cols])
    
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    store_features['cluster_id'] = kmeans.fit_predict(scaled_features)
    
    # 클러스터 이름 부여 (분석용 가이드)
    # 실제 프로젝트에서는 클러스터 특징별로 '오피스 상권', '주택가 상권' 등으로 명명 가능
    logger.info("4. 클러스터링 결과 DB 저장 중 (table: store_clusters)...")
    store_features['updated_at'] = pd.Timestamp.now()
    
    # DB 저장 (기존 정보 갱신)
    store_features.to_sql('store_clusters', engine, if_exists='replace', index=False)
    
    logger.info(f"✅ 매장 클러스터링 완료! (총 {len(store_features)}개 매장 분류, 소요시간: {time.time()-start_t:.1f}초)")
    print(store_features.groupby('cluster_id')[feature_cols].mean())

if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    run_store_clustering(db_url)
