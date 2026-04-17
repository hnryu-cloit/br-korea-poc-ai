import pytest
import pandas as pd
from services.chance_loss_engine import ChanceLossEngine

def test_ai_common_035_chance_loss_estimation():
    # 1. 테스트 데이터 준비 (11시에 판매가 0으로 끊긴 상황 연출)
    store_id = "S1"
    item_id = "I1"
    target_date = "20240115"
    unit_price = 1500  # 제품 단가 1,500원
    
    sales_data = [
        {"MASKED_STOR_CD": store_id, "ITEM_CD": item_id, "SALE_DT": target_date, "SALE_QTY": 10, "TMZON_DIV": "10"},
        {"MASKED_STOR_CD": store_id, "ITEM_CD": item_id, "SALE_DT": target_date, "SALE_QTY": 0, "TMZON_DIV": "11"},
        {"MASKED_STOR_CD": store_id, "ITEM_CD": item_id, "SALE_DT": target_date, "SALE_QTY": 10, "TMZON_DIV": "12"},
    ]
    sales_df = pd.DataFrame(sales_data)
    
    production_df = pd.DataFrame([
        {"MASKED_STOR_CD": store_id, "ITEM_CD": item_id, "PROD_DT": target_date, "PROD_QTY": 20}
    ])

    # 2. 엔진 실행
    engine = ChanceLossEngine()
    result = engine.estimate_chance_loss(
        sales_df=sales_df,
        production_df=production_df,
        store_id=store_id,
        item_id=item_id,
        target_date=target_date,
        unit_price=unit_price
    )

    # 결과 출력
    print(f"\n--- AI-COMMON-035 QA 검증 결과 ---")
    print(f"탐지된 품절 의심 구간: {result['zero_sale_periods']}")
    print(f"추정 손실 수량: {result['estimated_loss_qty']}개")
    print(f"추정 손실 금액: {result['estimated_loss_amount']}원")
    print(f"산출 신뢰도: {result['confidence']}")

    # 3. 결과 검증 (Assertion)
    assert "11:00" in result["zero_sale_periods"]
    assert result["estimated_loss_qty"] > 0
    assert result["estimated_loss_amount"] > 0
    assert result["confidence"] in ["high", "medium", "low"]

if __name__ == "__main__":
    test_ai_common_035_chance_loss_estimation()
