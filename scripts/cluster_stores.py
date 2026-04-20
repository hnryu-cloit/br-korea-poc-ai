import os
import sys
import logging
import pandas as pd
import numpy as np
import time

# KMeans 윈도우 메모리 릭 경고 방지 (가장 상단에 위치해야 함)
os.environ["OMP_NUM_THREADS"] = "1"

from sqlalchemy import create_engine
from sklearn.cluster import KMeans, DBSCAN
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

# HDBSCAN은 별도 설치가 필요할 수 있으므로 시도 후 실패 시 알림
try:
    from hdbscan import HDBSCAN
except ImportError:
    HDBSCAN = None

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cluster_champion_finder")

class ClusterChampionFinder:
    def __init__(self, data: pd.DataFrame, feature_cols: list):
        self.raw_data = data
        self.feature_cols = feature_cols
        self.scaler = StandardScaler()
        self.scaled_data = self.scaler.fit_transform(data[feature_cols])
        self.results = []

    def tune_kmeans(self, k_range=range(2, 7)):
        """K-Means의 최적 K를 찾고 결과를 기록합니다."""
        logger.info("tuning K-Means (Silhouette Method)...")
        best_score = -1
        best_k = 2
        
        for k in k_range:
            # n_init=10 및 random_state 고정으로 안정성 확보
            model = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = model.fit_predict(self.scaled_data)
            score = silhouette_score(self.scaled_data, labels)
            if score > best_score:
                best_score = score
                best_k = k
        
        self.results.append({
            'algorithm': 'K-Means',
            'best_k': best_k,
            'silhouette': best_score,
            'params': {'n_clusters': best_k}
        })
        return best_score

    def tune_dbscan(self, eps_range=[0.3, 0.5, 0.8], min_samples_range=[3, 5]):
        """DBSCAN의 최적 파라미터를 찾습니다."""
        logger.info("tuning DBSCAN (Grid Search)...")
        best_score = -1
        best_params = {}
        
        for eps in eps_range:
            for min_samples in min_samples_range:
                model = DBSCAN(eps=eps, min_samples=min_samples)
                labels = model.fit_predict(self.scaled_data)
                
                # 군집이 2개 이상일 때만 실루엣 계수 측정 가능 (노이즈 -1 제외)
                unique_labels = set(labels) - {-1}
                if len(unique_labels) < 2: continue
                
                score = silhouette_score(self.scaled_data, labels)
                if score > best_score:
                    best_score = score
                    best_params = {'eps': eps, 'min_samples': min_samples}
        
        if best_score > -1:
            # 챔피언 선정을 위해 실제 군집 수 계산
            final_labels = DBSCAN(**best_params).fit_predict(self.scaled_data)
            self.results.append({
                'algorithm': 'DBSCAN',
                'best_k': len(set(final_labels) - {-1}),
                'silhouette': best_score,
                'params': best_params
            })
        return best_score

    def tune_hdbscan(self):
        """HDBSCAN 성능을 측정합니다."""
        if HDBSCAN is None: 
            logger.info("HDBSCAN skip (module not installed)")
            return -1
        
        logger.info("evaluating HDBSCAN...")
        model = HDBSCAN(min_cluster_size=5, gen_min_span_tree=True)
        labels = model.fit_predict(self.scaled_data)
        
        unique_labels = set(labels) - {-1}
        if len(unique_labels) < 2: return -1
        
        score = silhouette_score(self.scaled_data, labels)
        self.results.append({
            'algorithm': 'HDBSCAN',
            'best_k': len(unique_labels),
            'silhouette': score,
            'params': {'min_cluster_size': 5}
        })
        return score

    def find_champion(self):
        """세 알고리즘 비교 후 챔피언 선정"""
        self.tune_kmeans()
        self.tune_dbscan()
        self.tune_hdbscan()
        
        if not self.results:
            logger.error("❌ 모든 알고리즘이 유효한 군집을 찾지 못했습니다.")
            return None
            
        champion = max(self.results, key=lambda x: x['silhouette'])
        logger.info(f"🏆 챔피언 알고리즘 선정: {champion['algorithm']} (Silhouette: {champion['silhouette']:.4f}, K: {champion['best_k']})")
        return champion

def load_behavioral_data(engine):
    """클러스터링을 위한 매장별 행동 피처 로드"""
    logger.info("1. 클러스터링용 데이터 로드 및 피처 생성 중...")
    
    # 기초 판매 데이터
    df_sales = pd.read_sql("SELECT masked_stor_cd, sale_dt, tmzon_div as hour, sale_qty FROM raw_daily_store_item_tmzon", engine)
    df_sales['sale_qty'] = pd.to_numeric(df_sales['sale_qty'], errors='coerce').fillna(0).astype(float)
    df_sales['is_weekend'] = (pd.to_datetime(df_sales['sale_dt']).dt.weekday >= 5).astype(int)
    
    # 피처 1~4 통합
    store_features = df_sales.groupby('masked_stor_cd').agg({
        'sale_qty': ['mean', 'std', 'sum'],
        'is_weekend': 'mean',
        'hour': lambda x: x.value_counts().index[0] if not x.empty else 12
    })
    store_features.columns = ['avg_qty', 'std_qty', 'total_qty', 'weekend_ratio', 'peak_hour']
    store_features = store_features.reset_index()
    
    # 피처 5: DB에서 온라인 매출 데이터 로드 및 비중 계산 (매출액 기준)
    logger.info("  1-1. DB에서 온/오프라인 매출 비중 분리 계산 중...")
    try:
        # ho_chnl_nm에 'POS'가 포함되지 않은 것들을 온라인(배달/픽업/기타)으로 간주
        query_online = """
            SELECT masked_stor_cd, 
                   SUM(CASE WHEN UPPER(ho_chnl_nm) NOT LIKE '%%POS%%' THEN CAST(sale_amt AS FLOAT) ELSE 0 END) as online_amt,
                   SUM(CAST(sale_amt AS FLOAT)) as total_amt
            FROM raw_daily_store_online
            GROUP BY masked_stor_cd
        """
        df_online_stats = pd.read_sql(query_online, engine)
        
        # ZeroDivisionError 방지를 위해 replace 사용
        df_online_stats['online_ratio_calc'] = (
            df_online_stats['online_amt'] / df_online_stats['total_amt'].replace(0, np.nan)
        ).fillna(0).replace([np.inf, -np.inf], 0)
        
        store_online_sum = df_online_stats[['masked_stor_cd', 'online_ratio_calc']]
    except Exception as e:
        logger.warning(f"온라인 채널 데이터 분석 실패. 온라인 비중을 0으로 처리합니다. (Error: {e})")
        store_online_sum = pd.DataFrame(columns=['masked_stor_cd', 'online_ratio_calc'])

    # 최종 피처 병합
    store_features = store_features.merge(store_online_sum, on='masked_stor_cd', how='left')
    store_features['online_ratio'] = store_features['online_ratio_calc'].fillna(0)
    store_features.drop(columns=['online_ratio_calc'], inplace=True, errors='ignore')
    
    return store_features

def visualize_clusters(df: pd.DataFrame, feature_cols: list, save_path: str = 'br-korea-poc-ai/scripts/cluster_result.png'):
    """
    PCA 차원축소를 통해 클러스터링 결과를 2D 산점도로 시각화하여 저장합니다.
    """
    import matplotlib.pyplot as plt
    from sklearn.decomposition import PCA
    
    logger.info(f"3. 시각화 이미지 생성 중... ({save_path})")
    
    # 숫자형 데이터로 변환
    X = df[feature_cols].apply(pd.to_numeric)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 2차원 축소
    pca = PCA(n_components=2)
    X_pca = pca.fit_transform(X_scaled)
    df_vis = df.copy()
    df_vis['PCA1'] = X_pca[:, 0]
    df_vis['PCA2'] = X_pca[:, 1]
    
    plt.figure(figsize=(10, 7))
    unique_clusters = sorted(df_vis['cluster_id'].unique())
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b']
    
    for idx, cluster in enumerate(unique_clusters):
        c_data = df_vis[df_vis['cluster_id'] == cluster]
        plt.scatter(c_data['PCA1'], c_data['PCA2'], 
                    c=colors[idx % len(colors)], label=f'Cluster {cluster}', 
                    s=100, alpha=0.8, edgecolors='k')
        
    plt.title(f"Store Clustering Visualization (PCA)", fontsize=14)
    plt.xlabel(f"PCA Component 1 ({pca.explained_variance_ratio_[0]*100:.1f}%)")
    plt.ylabel(f"PCA Component 2 ({pca.explained_variance_ratio_[1]*100:.1f}%)")
    plt.legend(title="Cluster ID")
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(save_path, dpi=300)
    plt.close()
    logger.info("✅ 시각화 이미지 저장 완료.")


def run_clustering_and_save(db_url: str):
    engine = create_engine(db_url)
    store_features = load_behavioral_data(engine)
    
    feature_cols = ['avg_qty', 'std_qty', 'weekend_ratio', 'peak_hour', 'online_ratio']
    
    # 챔피언 찾기
    finder = ClusterChampionFinder(store_features, feature_cols)
    champion_info = finder.find_champion()
    
    if not champion_info: return

    # 챔피언 모델로 최종 군집화 수행
    logger.info(f"2. {champion_info['algorithm']} 모델로 최종 군집화 수행 중...")
    scaler = StandardScaler()
    scaled_data = scaler.fit_transform(store_features[feature_cols])
    
    if champion_info['algorithm'] == 'K-Means':
        final_model = KMeans(random_state=42, n_init=10, **champion_info['params'])
    elif champion_info['algorithm'] == 'DBSCAN':
        final_model = DBSCAN(**champion_info['params'])
    else:
        from hdbscan import HDBSCAN
        final_model = HDBSCAN(**champion_info['params'])
        
    store_features['cluster_id'] = final_model.fit_predict(scaled_data)
    store_features['algorithm_used'] = champion_info['algorithm']
    store_features['updated_at'] = pd.Timestamp.now()
    
    # DB 저장
    store_features.to_sql('store_clusters', engine, if_exists='replace', index=False)
    logger.info(f"✅ 클러스터링 정보 업데이트 완료 (Table: store_clusters)")

    # [분석용] 시각화가 필요한 경우 아래 주석을 해제하세요.
    # visualize_clusters(store_features, feature_cols)

if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    run_clustering_and_save(db_url)
