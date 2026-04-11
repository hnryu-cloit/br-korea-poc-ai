import os
import pandas as pd
from datetime import datetime

class DataDataLoader:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    def load_item_sales(self):
        """상품별 매출 (DAILY_STOR_ITEM.xlsx)"""
        path = os.path.join(self.data_dir, "DAILY_STOR_ITEM.xlsx")
        df = pd.read_excel(path)
        # SALE_AMT: 판매금액, DC_AMT: 할인금액, ACTUAL_SALE_AMT: 실매출금액
        df['SALE_AMT'] = pd.to_numeric(df['SALE_AMT'], errors='coerce').fillna(0)
        df['DC_AMT'] = pd.to_numeric(df['DC_AMT'], errors='coerce').fillna(0)
        df['SALE_QTY'] = pd.to_numeric(df['SALE_QTY'], errors='coerce').fillna(0)
        return df

    def load_campaign_sales(self):
        """캠페인 매출 (DAILY_STOR_CPI.xlsx)"""
        path = os.path.join(self.data_dir, "DAILY_STOR_CPI.xlsx")
        df = pd.read_excel(path)
        return df

    def load_pay_way_sales(self):
        """결제수단별 매출 (DAILY_STOR_PAY_WAY.xlsx)"""
        path = os.path.join(self.data_dir, "DAILY_STOR_PAY_WAY.xlsx")
        df = pd.read_excel(path)
        df['PAY_AMT'] = pd.to_numeric(df['PAY_AMT'], errors='coerce').fillna(0)
        return df

    def load_pay_cd(self):
        """결제 코드 마스터 (PAY_CD.csv)"""
        path = os.path.join(self.data_dir, "PAY_CD.csv")
        df = pd.read_csv(path, encoding='cp949')
        return df

    def load_order_details(self):
        """주문 상세 내역 (ORD_DTL.xlsx)"""
        path = os.path.join(self.data_dir, "ORD_DTL.xlsx")
        df = pd.read_excel(path)
        # ORD_QTY: 주문수량, DLV_DT: 배송일자, STOR_CD: 매장코드, ITEM_NM: 상품명
        df['ORD_QTY'] = pd.to_numeric(df['ORD_QTY'], errors='coerce').fillna(0)
        return df

    def get_product_group_deadlines(self) -> dict:
        """
        제품 그룹별 주문 마감 시간 설정 (Mock DB)
        실제 운영 시에는 본사 설정 DB에서 조회해와야 합니다.
        """
        return {
            "도넛/완제품": "10:00",
            "냉동생지": "14:00",
            "커피/음료원부재료": "16:30",
            "포장재/기타": "18:00"
        }
