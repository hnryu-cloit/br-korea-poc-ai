import os
import sys
import logging
import pandas as pd
import numpy as np
import time

# KMeans 윈도우 메모리 릭 경고 방지
os.environ["OMP_NUM_THREADS"] = "1"

from sqlalchemy import create_engine
from sklearn.cluster import KMeans, DBSCAN
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

# HDBSCAN 시도
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
        # 데이터가 없을 경우에 대한 예외 처리
        if data.empty:
            self.scaled_data = np.array([])
        else:
            self.scaled_data = self.scaler.fit_transform(data[feature_cols])
        self.results = []

    def tune_kmeans(self, k_range=range(2, 6)):
        if self.scaled_data.size == 0: return -1
        logger.info("tuning K-Means (Silhouette Method)...")
        best_score, best_k = -1, 2
        for k in k_range:
            if k >= len(self.raw_data): break
            model = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = model.fit_predict(self.scaled_data)
            score = silhouette_score(self.scaled_data, labels)
            if score > best_score:
                best_score, best_k = score, k
        self.results.append({'algorithm': 'K-Means', 'best_k': best_k, 'silhouette': best_score, 'params': {'n_clusters': best_k}})
        return best_score

    def tune_dbscan(self, eps_range=[0.5, 1.0, 1.5], min_samples_range=[3, 5]):
        if self.scaled_data.size == 0: return -1
        logger.info("tuning DBSCAN (Grid Search)...")
        best_score, best_params = -1, {}
        for eps in eps_range:
            for min_samples in min_samples_range:
                model = DBSCAN(eps=eps, min_samples=min_samples)
                labels = model.fit_predict(self.scaled_data)
                if len(set(labels) - {-1}) < 2: continue
                score = silhouette_score(self.scaled_data, labels)
                if score > best_score:
                    best_score, best_params = score, {'eps': eps, 'min_samples': min_samples}
        if best_score > -1:
            final_labels = DBSCAN(**best_params).fit_predict(self.scaled_data)
            self.results.append({'algorithm': 'DBSCAN', 'best_k': len(set(final_labels) - {-1}), 'silhouette': best_score, 'params': best_params})
        return best_score

    def tune_hdbscan(self):
        if HDBSCAN is None or self.scaled_data.size == 0: return -1
        logger.info("evaluating HDBSCAN...")
        model = HDBSCAN(min_cluster_size=3, gen_min_span_tree=True)
        labels = model.fit_predict(self.scaled_data)
        if len(set(labels) - {-1}) < 2: return -1
        score = silhouette_score(self.scaled_data, labels)
        self.results.append({'algorithm': 'HDBSCAN', 'best_k': len(set(labels) - {-1}), 'silhouette': score, 'params': {'min_cluster_size': 3}})
        return score

    def find_champion(self):
        self.tune_kmeans()
        self.tune_dbscan()
        self.tune_hdbscan()
        if not self.results:
            logger.error("❌ 유효한 군집을 찾지 못했습니다.")
            return None
        champion = max(self.results, key=lambda x: x['silhouette'])
        logger.info(f"🏆 챔피언 선정: {champion['algorithm']} (Silhouette: {champion['silhouette']:.4f}, K: {champion['best_k']})")
        return champion

def load_behavioral_data(engine):
    """안정적인 매출 규모(로그) 및 행동 지표 로드"""
    logger.info("1. 클러스터링용 데이터 로드 (안정화 버전)...")
    
    # 기초 판매 데이터 (대문자 우선 조회)
    try:
        query = 'SELECT "masked_stor_cd", "sale_dt", "tmzon_div" as "hour", "sale_qty" FROM "DAILY_STOR_ITEM_TMZON"'
        df_sales = pd.read_sql(query, engine)
    except:
        query = "SELECT masked_stor_cd, sale_dt, tmzon_div as hour, sale_qty FROM raw_daily_store_item_tmzon"
        df_sales = pd.read_sql(query, engine)
    
    df_sales.columns = [c.lower() for c in df_sales.columns]
    df_sales['sale_qty'] = pd.to_numeric(df_sales['sale_qty'], errors='coerce').fillna(0).astype(float)
    df_sales['hour'] = pd.to_numeric(df_sales['hour'], errors='coerce').fillna(12).astype(int)
    
    # 1-1. 매장별 기초 집계 (평균 매출, 주말 비중, 피크 타임)
    df_sales['is_weekend'] = (pd.to_datetime(df_sales['sale_dt'], format='%Y%m%d', errors='coerce').dt.weekday >= 5).astype(int)
    
    store_stats = df_sales.groupby('masked_stor_cd').agg({
        'sale_qty': ['mean', 'std', 'sum'],
        'hour': lambda x: x.value_counts().index[0] if not x.empty else 12,
        'is_weekend': 'mean'
    })
    store_stats.columns = ['avg_qty', 'std_qty', 'total_qty', 'peak_hour', 'weekend_ratio']
    store_stats = store_stats.reset_index()

    # [High Priority] 매출액 로그 변환 적용 (이상치 격리 방지)
    store_stats['log_avg_qty'] = np.log1p(store_stats['avg_qty'])
    
    # 1-2. DB에서 온라인 매출 비중 분리 계산
    try:
        query_online = """
            SELECT masked_stor_cd, 
                   SUM(CASE WHEN UPPER(ho_chnl_nm) NOT LIKE '%%POS%%' THEN CAST(sale_amt AS FLOAT) ELSE 0 END) as online_amt,
                   SUM(CAST(sale_amt AS FLOAT)) as total_amt
            FROM raw_daily_store_online
            GROUP BY masked_stor_cd
        """
        df_on_stats = pd.read_sql(query_online, engine)
        df_on_stats['online_ratio'] = (df_on_stats['online_amt'] / df_on_stats['total_amt'].replace(0, np.nan)).fillna(0)
        store_stats = store_stats.merge(df_on_stats[['masked_stor_cd', 'online_ratio']], on='masked_stor_cd', how='left').fillna(0)
    except:
        store_stats['online_ratio'] = 0.0

    return store_stats

def visualize_clusters(df: pd.DataFrame, feature_cols: list, save_path: str = 'br-korea-poc-ai/results/store_clustering_result.png'):
    import matplotlib.pyplot as plt
    from sklearn.decomposition import PCA
    import matplotlib.font_manager as fm
    
    # 폰트 설정
    plt.rc('font', family='Malgun Gothic')
    plt.rcParams['axes.unicode_minus'] = False
    
    if df.empty: return
    X = StandardScaler().fit_transform(df[feature_cols])
    pca_data = PCA(n_components=2).fit_transform(X)
    plt.figure(figsize=(10, 7))
    
    colors = ['#4a90e2', '#e74c3c', '#2ecc71', '#f1c40f', '#9b59b6', '#34495e']
    for idx, c in enumerate(sorted(df['cluster_id'].unique())):
        mask = df['cluster_id'] == c
        color = colors[idx % len(colors)]
        plt.scatter(pca_data[mask, 0], pca_data[mask, 1], label=f'Cluster {c}', s=100, color=color, edgecolors='k', alpha=0.7)
    
    plt.title("선정된 알고리즘 기반 매장 군집화 결과 (PCA Visual)", fontsize=14, fontweight='bold', pad=20)
    plt.xlabel('PCA Component 1', fontsize=10)
    plt.ylabel('PCA Component 2', fontsize=10)
    plt.legend(loc='best')
    plt.grid(True, alpha=0.3)
    
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.close()

def visualize_algorithm_comparison(results: list, save_path: str = 'br-korea-poc-ai/results/clustering_algorithm_comparison.png'):
    """실제 클러스터링 알고리즘 평가 결과를 시각화"""
    import matplotlib.pyplot as plt
    plt.rc('font', family='Malgun Gothic')
    plt.rcParams['axes.unicode_minus'] = False
    
    if not results: return
    
    df_algo = pd.DataFrame(results)
    
    plt.figure(figsize=(8, 6))
    champion_algo = max(results, key=lambda x: x['silhouette'])['algorithm']
    
    colors = ['#4a90e2' if row['algorithm'] == champion_algo else '#cccccc' for _, row in df_algo.iterrows()]
    bars = plt.bar(df_algo['algorithm'], df_algo['silhouette'], color=colors, width=0.6)

    plt.title('클러스터링 알고리즘별 실루엣 계수 비교 (최적 모델 선정)', fontsize=14, fontweight='bold', pad=20)
    plt.ylabel('실루엣 계수 (Silhouette Score)', fontsize=12)
    plt.ylim(0, max(df_algo['silhouette']) + 0.2)

    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height + 0.02,
                 f'{height:.4f}', ha='center', va='bottom', fontsize=11, fontweight='bold')

    plt.text(len(results)-1.5, max(df_algo['silhouette']) + 0.1, f'★ Champion: {champion_algo}', ha='center', color='#4a90e2', fontsize=12, fontweight='bold')

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.tight_layout()
    plt.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.close()

def run_clustering_and_save(db_url: str):
    engine = create_engine(db_url)
    store_features = load_behavioral_data(engine)
    if store_features.empty:
        logger.error("❌ 분석할 데이터가 없습니다. DB 적재 상태를 확인하세요.")
        return

    # 피처 세트: 로그 변환된 매출 규모 + 행동 지표들
    feature_cols = ['log_avg_qty', 'weekend_ratio', 'peak_hour', 'online_ratio']
    
    finder = ClusterChampionFinder(store_features, feature_cols)
    champion = finder.find_champion()
    
    # [시각화 1] 알고리즘 평가 결과 그래프 저장
    visualize_algorithm_comparison(finder.results)
    
    if not champion: return

    # 최종 적용
    model = KMeans(random_state=42, **champion['params']) if champion['algorithm'] == 'K-Means' else \
            (DBSCAN(**champion['params']) if champion['algorithm'] == 'DBSCAN' else HDBSCAN(**champion['params']))
    
    X_scaled = StandardScaler().fit_transform(store_features[feature_cols])
    store_features['cluster_id'] = model.fit_predict(X_scaled)
    store_features['algorithm_used'] = champion['algorithm']
    store_features['updated_at'] = pd.Timestamp.now()
    
    store_features.to_sql('store_clusters', engine, if_exists='replace', index=False)
    logger.info("✅ 클러스터링 완료 및 DB 저장 성공.")
    
    # [시각화 2] 챔피언 알고리즘 기반 PCA 산점도 저장
    visualize_clusters(store_features, feature_cols)
    logger.info("✅ 2종 클러스터링 시각화 자료 (결과 폴더 내) 생성 완료.")

if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    run_clustering_and_save(db_url)
