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
    # 최근 3개월 이상의 충분한 데이터를 기반으로 분석 (안정적 패턴 도출)
    query = """
        SELECT masked_stor_cd, sale_dt, tmzon_div as hour, sale_qty 
        FROM raw_daily_store_item_tmzon
    """
    df = pd.read_sql(query, engine)
    df['sale_qty'] = pd.to_numeric(df['sale_qty'], errors='coerce').fillna(0).astype(float)
    df['sale_dt'] = pd.to_datetime(df['sale_dt'], format='%Y%m%d')
    df['weekday'] = df['sale_dt'].dt.weekday
    df['is_weekend'] = (df['weekday'] >= 5).astype(int)
    
    logger.info("2. 매장별 행동 피처(Behavioral Features) 추출 중...")
    # 피처 1: 매출 규모 (평균 판매량)
    # 피처 2: 주말 매출 비중
    # 피처 3: 피크 시간대 (가장 판매가 집중되는 시간)
    # 피처 4: 매출 변동성 (표준편차)
    store_features = df.groupby('masked_stor_cd').agg({
        'sale_qty': ['mean', 'std'],
        'is_weekend': 'mean',
        'hour': lambda x: x.value_counts().index[0] if not x.empty else 12
    })
    
    store_features.columns = ['avg_qty', 'std_qty', 'weekend_ratio', 'peak_hour']
    store_features = store_features.fillna(0).reset_index()
    
    logger.info("3. K-Means 클러스터링 수행 중...")
    feature_cols = ['avg_qty', 'std_qty', 'weekend_ratio', 'peak_hour']
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
