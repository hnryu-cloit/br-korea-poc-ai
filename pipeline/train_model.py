import os
import sys
import pandas as pd
import logging

# 모듈 경로 설정
ai_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ai_dir not in sys.path:
    sys.path.insert(0, ai_dir)

from services.inventory_predictor import InventoryPredictor

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_pipeline")

def run_training_pipeline():
    """
    [Optimized DB Training] 
    - DB에서 직접 데이터를 로드하여 고도화된 예측 모델을 학습합니다.
    """
    logger.info("🚀 DB 데이터를 활용한 고도화 ML 모델 학습 파이프라인 시작...")

    db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
    engine = create_engine(db_url)
    
    # 1. 대상 기간 설정 (최근 2개월 + 작년 동월 3개월)
    recent_start, recent_end = "20260101", "20260228"
    last_year_start, last_year_end = "20250101", "20250331"
    
    query = text('SELECT masked_stor_cd AS "MASKED_STOR_CD", item_cd AS "ITEM_CD", sale_dt AS "SALE_DT", tmzon_div AS "TMZON_DIV", sale_qty AS "SALE_QTY" FROM raw_daily_store_item_tmzon')

    try:
        logger.info("DB에서 전체 학습 데이터 로드 중...")
        full_df = pd.read_sql(query, engine)
        logger.info(f"✅ 원본 데이터 로드 완료: {len(full_df)} 행")
        
        # SALE_DT 및 ITEM_CD를 문자열로 통일 (타입 버그 방지)
        full_df['SALE_DT'] = full_df['SALE_DT'].astype(str)
        full_df['ITEM_CD'] = full_df['ITEM_CD'].astype(str)
        
        # 1-1. 기간 필터링
        mask = ((full_df['SALE_DT'] >= recent_start) & (full_df['SALE_DT'] <= recent_end)) | \
               ((full_df['SALE_DT'] >= last_year_start) & (full_df['SALE_DT'] <= last_year_end))
        full_df = full_df[mask]
        logger.info(f"✅ 기간 필터링 후 데이터: {len(full_df)} 행")
        
        # 1-2. [초고도화] 프로모션 데이터 클렌징 (순수 베이스라인 학습용)
        logger.info("프로모션(캠페인) 이력 로드 중...")
        campaign_query = text('''
            SELECT 
                m.start_dt, m.fnsh_dt, i.item_cd 
            FROM raw_campaign_master m
            JOIN raw_campaign_item i ON m.cmp_cd = i.cmp_cd
            WHERE m.use_yn = 'Y' AND i.use_yn = 'Y'
        ''')
        campaign_df = pd.read_sql(campaign_query, engine)
        
        if not campaign_df.empty:
            campaign_df['start_dt'] = pd.to_datetime(campaign_df['start_dt']).dt.strftime('%Y%m%d')
            campaign_df['fnsh_dt'] = pd.to_datetime(campaign_df['fnsh_dt']).dt.strftime('%Y%m%d')
            campaign_df['item_cd'] = campaign_df['item_cd'].astype(str)
            
            # 프로모션에 해당하는 행을 찾기 위한 마스크
            promo_mask = pd.Series(False, index=full_df.index)
            for _, row in campaign_df.iterrows():
                # 해당 상품이, 해당 프로모션 기간 내에 팔린 기록이면 True
                promo_mask |= (full_df['ITEM_CD'] == row['item_cd']) & \
                              (full_df['SALE_DT'] >= row['start_dt']) & \
                              (full_df['SALE_DT'] <= row['fnsh_dt'])
            
            # 프로모션 데이터(True)가 아닌(~) 순수 일반 판매 데이터만 남김
            clean_df = full_df[~promo_mask]
            removed_count = len(full_df) - len(clean_df)
            logger.info(f"✅ 프로모션(노이즈) 데이터 {removed_count}행 제거 완료. 순수 데이터: {len(clean_df)} 행")
            full_df = clean_df
        else:
            logger.info("적용된 프로모션 이력이 없습니다.")
            
    except Exception as e:
        logger.error(f"DB 데이터 로드/클렌징 중 오류 발생: {e}")
        return

    if full_df.empty:
        logger.error("학습할 순수 데이터가 없습니다.")
        return


    # 2. 학습/검증 분리 및 학습
    full_df = full_df.sort_values('SALE_DT')
    split_idx = int(len(full_df) * 0.8)
    train_df = full_df.iloc[:split_idx]
    test_df = full_df.iloc[split_idx:]

    predictor = InventoryPredictor(model_dir=os.path.join(ai_dir, "models"))
    predictor.train(train_df)

    # 3. 성능 검증
    metrics = predictor.evaluate(test_df)
    if metrics:
        logger.info(f"🏆 고도화 모델 검증 결과:")
        logger.info(f"  - MAE : {metrics.get('MAE'):.4f}")
        logger.info(f"  - MAPE: {metrics.get('MAPE'):.2f}%")
        logger.info(f"  - RMSE: {metrics.get('RMSE'):.4f}")
        logger.info(f"  - R2  : {metrics.get('R2'):.4f}")
    else:
        logger.warning("성능 검증 지표를 산출할 수 없습니다.")
        
    logger.info("✅ 모델 최적화 학습 종료.")


if __name__ == "__main__":
    run_training_pipeline()
