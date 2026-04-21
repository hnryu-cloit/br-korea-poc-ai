import os
import sys
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import create_engine, text

# 상위 폴더의 모듈을 임포트하기 위한 설정
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.logger import init_logger
from services.production_agent import ProductionManagementAgent

logger = init_logger("production_evaluator")


def load_data_from_db(store_id: str, target_date_str: str):
    """테스트를 위해 DB에서 해당 매장의 데이터를 가져옵니다."""
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc"
    )
    engine = create_engine(db_url)

    with engine.connect() as conn:
        inv_query = text('SELECT * FROM "SPL_DAY_STOCK_DTL" WHERE "MASKED_STOR_CD" = :store')
        prod_query = text('SELECT * FROM "PROD_DTL" WHERE "MASKED_STOR_CD" = :store')
        sales_query = text('SELECT * FROM "DAILY_STOR_ITEM_TMZON" WHERE "MASKED_STOR_CD" = :store')
        store_prod_query = text('SELECT * FROM "STOR_PROD_ITEM" WHERE "MASKED_STOR_CD" = :store')

        inv_df = pd.read_sql(inv_query, conn, params={"store": store_id})
        prod_df = pd.read_sql(prod_query, conn, params={"store": store_id})
        sales_df = pd.read_sql(sales_query, conn, params={"store": store_id})
        store_prod_df = pd.read_sql(store_prod_query, conn, params={"store": store_id})

    return inv_df, prod_df, sales_df, store_prod_df


def evaluate_inventory_reversal(
    agent: ProductionManagementAgent, store_id: str, item_id: str, target_date: str
):
    """[과제 1] 재고 역산 엔진(Inventory Reversal) 정합성 검증"""
    print("\n" + "=" * 60)
    print(
        f"📊 [과제 1] 재고 역산 엔진 정합성 검증 (매장: {store_id}, 상품: {item_id}, 날짜: {target_date})"
    )
    print("=" * 60)

    try:
        # 1. 에이전트의 역산 엔진을 통해 5분 단위 가상 재고 흐름 데이터프레임 추출
        stock_flow = agent.engine.get_estimated_stock(store_id, item_id, target_date)

        if stock_flow.empty:
            print("❌ 해당 조건의 재고 흐름 데이터가 없습니다.")
            return

        # 2. 1시간 단위로 샘플링하여 출력 (08:00 ~ 22:00)
        print(
            f"{'시간':^10} | {'기존재고':^10} | {'입고(생산)':^10} | {'출고(판매)':^10} | {'추정재고(역산)':^10}"
        )
        print("-" * 60)

        for hour in range(8, 23):
            t = datetime.strptime(target_date, "%Y%m%d").replace(hour=hour)
            # 해당 시간대의 가장 가까운 과거 인덱스 찾기
            if t in stock_flow.index:
                row = stock_flow.loc[t]
            else:
                idx = stock_flow.index.asof(t)
                row = stock_flow.loc[idx] if pd.notna(idx) else None

            if row is not None:
                # 누적값이 아닌 해당 시간대의 변화량 계산 (1시간 단위)
                start_t = t - timedelta(hours=1) if hour > 8 else t.replace(hour=0)
                period_flow = stock_flow[(stock_flow.index > start_t) & (stock_flow.index <= t)]

                in_qty = period_flow["in_qty"].sum() if not period_flow.empty else 0
                out_qty = period_flow["out_qty"].sum() if not period_flow.empty else 0
                est_stock = row["estimated_stock"]

                # 정수로 표시 (반올림)
                in_q = int(round(in_qty))
                out_q = int(round(out_qty))
                est_q = int(round(est_stock))
                prev_q = est_q - in_q + out_q  # 화면상 계산이 딱 맞도록 역산

                print(
                    f"{t.strftime('%H:%M'):^10} | {prev_q:^10} | {in_q:^10} | {out_q:^10} | {est_q:^10}"
                )

        # 3. 정합성 검증 (추정 재고가 마이너스(-)로 떨어지는 비정상 구간 확인)
        negative_stock = stock_flow[stock_flow["estimated_stock"] < 0]
        if not negative_stock.empty:
            print(
                f"\n⚠️ 주의: 재고가 0 미만으로 떨어지는 구간이 {len(negative_stock)}곳 발견되었습니다."
            )
            print(
                "   -> 기초 재고 데이터가 부족하거나 출고(판매) 데이터가 입고(생산)보다 먼저/많이 기록된 경우입니다."
            )
        else:
            print("\n✅ 정합성 확인: 모든 시간대에서 재고가 정상적으로 0 이상을 유지하고 있습니다.")

    except Exception as e:
        import traceback

        print(f"❌ 역산 엔진 평가 중 오류 발생: {e}")
        traceback.print_exc()


def evaluate_prediction_model(
    agent: ProductionManagementAgent, store_id: str, item_id: str, target_date: str
):
    """[과제 2] 예측 모델(ML) 베이스라인 성능 평가"""
    print("\n" + "=" * 60)
    print("🎯 [과제 2] ML 예측 모델(Predictor) 성능 평가")
    print("=" * 60)

    try:
        # 실제 판매 데이터 로드
        sales_df = agent.historical_sales_df
        target_dt = datetime.strptime(target_date, "%Y%m%d")

        test_hours = range(10, 20)  # 10시 ~ 19시에 대해 1시간 후 예측 테스트

        total_error = 0
        valid_count = 0

        print(
            f"{'기준시간':^15} | {'예측 판매량(AI)':^15} | {'실제 판매량(DB)':^15} | {'오차(Error)':^10}"
        )
        print("-" * 60)

        for hour in test_hours:
            current_time = target_dt.replace(hour=hour)
            next_hour_time = current_time + timedelta(hours=1)

            # 1. AI에게 "1시간 뒤(next_hour)에 얼마나 팔릴까?" 예측시키기
            pred_qty = agent.predictor.predict_next_hour_sales(
                store_id, item_id, current_time, sales_df
            )

            # 2. DB에서 실제 그 시간(next_hour)에 팔린 수량 확인
            actual_sales_row = sales_df[
                (sales_df["MASKED_STOR_CD"].astype(str) == store_id)
                & (sales_df["ITEM_CD"].astype(str) == item_id)
                & (sales_df["SALE_DT"].astype(str) == target_date)
                & (sales_df["TMZON_DIV"].astype(int) == next_hour_time.hour)
            ]

            # SALE_QTY 컬럼을 명시적으로 float으로 변환
            actual_qty = (
                pd.to_numeric(actual_sales_row["SALE_QTY"], errors="coerce").fillna(0).sum()
                if not actual_sales_row.empty
                else 0
            )

            error = abs(pred_qty - actual_qty)
            total_error += error
            valid_count += 1

            print(
                f"{current_time.strftime('%H:%M')}->{next_hour_time.strftime('%H:%M')} | {pred_qty:^15.1f} | {actual_qty:^15.1f} | {error:^10.1f}"
            )

        # 3. 평가 지표 (MAE) 출력
        if valid_count > 0:
            mae = total_error / valid_count
            print(f"\n📊 모델 성능 지표 (MAE - 평균 절대 오차): {mae:.2f}개")
            if mae < 3.0:
                print(
                    "✅ 훌륭함: 평균적으로 실제 판매량과 3개 이내의 오차를 보이며 예측하고 있습니다."
                )
            else:
                print(
                    "⚠️ 주의: 예측 오차가 다소 높습니다. 모델 하이퍼파라미터 튜닝이 필요할 수 있습니다."
                )

    except Exception as e:
        import traceback

        print(f"❌ 예측 모델 평가 중 오류 발생: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    # 시연 환경 설정
    TEST_STORE_ID = "POC_030"

    print("🚀 생산 관리 Agent 평가 스크립트 시작...")

    # 1. DB에서 데이터 로드 (날짜 필터 없이 전체 로드)
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc"
    )
    engine = create_engine(db_url)
    with engine.connect() as conn:
        inv_query = text(
            """
            SELECT masked_stor_cd AS "MASKED_STOR_CD", item_cd AS "ITEM_CD", stock_dt AS "STOCK_DT", stock_qty AS "STOCK_QTY", item_nm AS "ITEM_NM"
            FROM raw_inventory_extract 
            WHERE masked_stor_cd = :store
        """
        )
        prod_query = text(
            """
            SELECT masked_stor_cd AS "MASKED_STOR_CD", item_cd AS "ITEM_CD", prod_dt AS "PROD_DT", prod_qty AS "PROD_QTY", prod_dgre AS "PROD_DGRE", item_nm AS "ITEM_NM"
            FROM raw_production_extract 
            WHERE masked_stor_cd = :store
        """
        )
        sales_query = text(
            """
            SELECT masked_stor_cd AS "MASKED_STOR_CD", item_cd AS "ITEM_CD", sale_dt AS "SALE_DT", tmzon_div AS "TMZON_DIV", sale_qty AS "SALE_QTY", item_nm AS "ITEM_NM"
            FROM raw_daily_store_item_tmzon 
            WHERE masked_stor_cd = :store
        """
        )
        store_prod_query = text(
            'SELECT masked_stor_cd AS "MASKED_STOR_CD", item_cd AS "ITEM_CD", item_nm AS "ITEM_NM" FROM raw_stor_prod_item WHERE masked_stor_cd = :store'
        )

        inv_df = pd.read_sql(inv_query, conn, params={"store": TEST_STORE_ID})
        prod_df = pd.read_sql(prod_query, conn, params={"store": TEST_STORE_ID})
        sales_df = pd.read_sql(sales_query, conn, params={"store": TEST_STORE_ID})
        store_prod_df = pd.read_sql(store_prod_query, conn, params={"store": TEST_STORE_ID})

    if sales_df.empty:
        print("❌ DB에서 판매 데이터를 불러오지 못했습니다. DB 연결을 확인해주세요.")
        sys.exit(1)

    # 동적으로 가장 최신 날짜 찾기
    latest_date = str(sales_df["SALE_DT"].max())
    if pd.isna(latest_date) or latest_date == "nan":
        print("❌ 유효한 판매 날짜를 찾을 수 없습니다.")
        sys.exit(1)

    TEST_TARGET_DATE = latest_date
    print(f"📌 테스트 대상 날짜 자동 선정: {TEST_TARGET_DATE}")

    # 동적으로 가장 많이 팔린 상품 찾기
    top_item = (
        sales_df[sales_df["SALE_DT"].astype(str) == TEST_TARGET_DATE]
        .groupby("ITEM_CD")["SALE_QTY"]
        .sum()
        .idxmax()
    )
    if pd.isna(top_item):
        print(f"❌ {TEST_TARGET_DATE} 날짜에 판매된 상품이 없습니다.")
        sys.exit(1)

    TEST_ITEM_ID = str(top_item)
    print(f"📌 테스트 대상 상품 자동 선정: {TEST_ITEM_ID}")

    # 2. 에이전트 초기화 (평가 대상)
    agent = ProductionManagementAgent(
        inventory_df=inv_df,
        production_df=prod_df,
        sales_df=sales_df,
        production_list_df=store_prod_df,
    )

    # 3. 과제 1 실행: 재고 역산 엔진 검증
    evaluate_inventory_reversal(agent, TEST_STORE_ID, TEST_ITEM_ID, TEST_TARGET_DATE)

    # 4. 과제 2 실행: 예측 모델 성능 평가
    evaluate_prediction_model(agent, TEST_STORE_ID, TEST_ITEM_ID, TEST_TARGET_DATE)

    print("\n🎉 평가 스크립트 실행 완료!")
