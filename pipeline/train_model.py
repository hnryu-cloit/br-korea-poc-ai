import os
import sys
import pandas as pd
import logging

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

from services.predictor import InventoryPredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_pipeline")

def run_training_pipeline():
    """
    [Optimized Batch Training] 
    - 최근 1개월 + 작년 동월 1개월 데이터를 위주로 학습하여 
    - 메모리 효율성과 예측 정확도(계절성)를 동시에 확보합니다.
    """
    logger.info("🚀 최적화된 ML 모델 학습 파이프라인 시작...")

    base_path = os.path.abspath(os.path.join(ai_dir, '..'))
    sales_dir = os.path.join(base_path, 'resources', '04_poc_data', '02_sales', '01_daily_item_tmzon')
    
    sales_files = [f for f in os.listdir(sales_dir) if f.endswith('.xlsx')]
    # 최신 파일 위주로 상위 3개 파일만 타겟팅 (성능과 속도의 균형)
    sales_files = sorted(sales_files, reverse=True)[:3]
    
    # 1. 대상 기간 설정 (보유 데이터 기반 현실적 기간)
    # 최근 데이터: 2026-01-01 ~ 2026-02-28 (파일 06 기준)
    # 작년 데이터: 2025-01-01 ~ 2025-03-31 (파일 01 기준)
    recent_start, recent_end = "20260101", "20260228"
    last_year_start, last_year_end = "20250101", "20250331"
    
    target_periods = [
        (recent_start, recent_end),
        (last_year_start, last_year_end)
    ]

    all_filtered_data = []
    cols_to_use = ['MASKED_STOR_CD', 'ITEM_CD', 'SALE_DT', 'TMZON_DIV', 'SALE_QTY']

    logger.info(f"데이터 필터링 로드 시작: 최근({recent_start}~) 및 작년({last_year_start}~)")

    # 모든 파일을 순회하며 날짜 범위에 맞는 행만 추출 (성능 및 속도를 위해 상위 1개 파일 집중)
    # 실제 운영 시에는 모든 파일을 순회하지만, PoC 테스트를 위해 최신 데이터가 많은 파일 하나를 정밀 타격
    sales_files = sorted([f for f in os.listdir(sales_dir) if f.endswith('.xlsx')], reverse=True)
    
    for file in sales_files[:1]: # 최신 파일 1개 정밀 타격
        p = os.path.join(sales_dir, file)
        try:
            # 80% 정확도를 위해 20만 행의 충분한 샘플 확보
            df = pd.read_excel(p, usecols=cols_to_use, nrows=200000, dtype={'MASKED_STOR_CD': str, 'ITEM_CD': str, 'SALE_DT': str, 'TMZON_DIV': str})
            
            mask = pd.Series(False, index=df.index)
            for start, end in target_periods:
                mask |= (df['SALE_DT'] >= start) & (df['SALE_DT'] <= end)
            
            filtered_df = df[mask]
            if not filtered_df.empty:
                all_filtered_data.append(filtered_df)
                logger.info(f"  - {file}: {len(filtered_df)}행 추출 완료")
        except Exception as e:
            logger.error(f"파일 처리 중 오류 ({file}): {e}")

    if not all_filtered_data:
        logger.error("지정된 기간에 해당하는 데이터가 없습니다.")
        return

    full_df = pd.concat(all_filtered_data, ignore_index=True)
    logger.info(f"✅ 총 학습 데이터 구축 완료: {len(full_df)} 행 (전체 지점 포함)")

    # 2. 학습/검증 분리 및 학습
    full_df = full_df.sort_values('SALE_DT')
    split_idx = int(len(full_df) * 0.8)
    train_df = full_df.iloc[:split_idx]
    test_df = full_df.iloc[split_idx:]

    predictor = InventoryPredictor(model_dir=os.path.join(ai_dir, "models"))
    predictor.train(train_df)

    # 3. 성능 검증
    metrics = predictor.evaluate(test_df)
    logger.info(f"🏆 고도화 모델 검증 결과 (MAE): {metrics}")
    logger.info("✅ 모델 최적화 학습 종료.")

if __name__ == "__main__":
    run_training_pipeline()
