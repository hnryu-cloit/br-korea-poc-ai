import os
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Load env
load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from common.gemini import Gemini
from services.rag_service import KnowledgeDocument

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5435/br_korea_poc")
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(bind=engine)


def generate_insights():
    print("🚀 매장별 동적 매출 인사이트(Level 2) 분석 및 벡터 DB 적재를 시작합니다...")
    gemini = Gemini()
    session = SessionLocal()

    # 1. 대상 점포 목록 가져오기 (샘플 테스트를 위해 우선 10개만 진행하거나 전체를 가져옴)
    # 전체 33개 점포이므로 모두 가져옴
    try:
        stor_df = pd.read_sql(
            'SELECT DISTINCT "MASKED_STOR_CD", "MAKED_STOR_NM" FROM "STOR_MST" WHERE "MASKED_STOR_CD" IS NOT NULL LIMIT 33',
            engine,
        )
    except Exception as e:
        print(f"점포 목록 조회 실패: {e}")
        return

    docs_to_insert = []

    for idx, row in stor_df.iterrows():
        store_cd = row["MASKED_STOR_CD"]
        store_nm = row["MAKED_STOR_NM"]

        if pd.isna(store_cd) or store_cd == "nan":
            continue

        print(f"[{idx+1}/{len(stor_df)}] 점포 분석 중: {store_nm}({store_cd})")

        # 2. 해당 점포의 총 매출 및 판매수량 상위 3개 상품 집계 (SQL)
        query = f"""
        SELECT 
            "ITEM_NM", 
            SUM("SALE_QTY") as total_qty, 
            SUM("ACTUAL_SALE_AMT") as total_amt
        FROM "DAILY_STOR_ITEM"
        WHERE "MASKED_STOR_CD" = '{store_cd}'
        GROUP BY "ITEM_NM"
        ORDER BY total_qty DESC
        LIMIT 3
        """

        try:
            sales_df = pd.read_sql(query, engine)

            if sales_df.empty:
                print("  -> 매출 데이터가 없습니다. 건너뜁니다.")
                continue

            # 데이터 문자열화
            total_store_amt = sales_df["total_amt"].sum()
            top_items = []
            for _, item_row in sales_df.iterrows():
                top_items.append(f"{item_row['ITEM_NM']}({int(item_row['total_qty'])}개)")

            data_context = f"점포명: {store_nm}, 총매출규모(상위3품목합): {int(total_store_amt):,}원, 가장 많이 팔린 3가지 상품: {', '.join(top_items)}"

            # 3. LLM에게 인사이트 문장 생성 요청
            prompt = f"""
            당신은 프랜차이즈 매출 분석가입니다.
            다음은 특정 매장의 매출 상위 3개 상품과 그 합산 매출 규모 데이터입니다.
            이 데이터를 바탕으로, 이 매장의 판매 특성을 설명하는 "자연스러운 비즈니스 인사이트 1문장"을 작성해주세요.
            (예시: "이 매장은 총 5백만원 규모의 매출을 내고 있으며, 특히 아이스 아메리카노와 카페라떼 같은 커피 음료의 판매 비중이 압도적으로 높습니다.")
            
            데이터: {data_context}
            """

            # 텍스트 생성
            insight_text = gemini.call_gemini_text(prompt)
            # 불필요한 줄바꿈이나 기호 제거
            insight_text = insight_text.replace("\n", " ").replace('"', "").strip()

            print(f"  -> AI 분석 결과: {insight_text}")

            # 4. 분석된 문장을 임베딩하여 RAG 문서 형태로 준비
            final_content = f"[{store_nm} 매장 분석 인사이트] {insight_text}"

            docs_to_insert.append(
                {
                    "doc_id": f"STORE_INSIGHT_{store_cd}",
                    "category": "Store_Insight",
                    "content": final_content,
                    "source": "LLM Generated Insight (DAILY_STOR_ITEM)",
                    "metadata": {"store_cd": store_cd, "store_nm": store_nm, "type": "insight"},
                }
            )

        except Exception as e:
            print(f"  -> 분석 실패: {e}")

    # 5. 벡터 DB에 일괄 적재
    print(f"\n총 {len(docs_to_insert)}개의 분석 인사이트를 벡터 DB에 적재합니다...")
    inserted = 0
    for doc in docs_to_insert:
        existing = session.query(KnowledgeDocument).filter_by(doc_id=doc["doc_id"]).first()
        if existing:
            continue

        try:
            embedding = gemini.get_embeddings(doc["content"], model="gemini-embedding-001")

            db_doc = KnowledgeDocument(
                doc_id=doc["doc_id"],
                category=doc["category"],
                content=doc["content"],
                source=doc["source"],
                metadata_=doc.get("metadata", {}),
                embedding=embedding,
            )
            session.add(db_doc)
            inserted += 1
            if inserted % 10 == 0:
                session.commit()
        except Exception as e:
            print(f"임베딩 실패 {doc['doc_id']}: {e}")
            session.rollback()

    session.commit()
    session.close()
    print(f"✅ 벡터 DB(Level 2) 인사이트 적재 완료: {inserted}건 추가됨.")


if __name__ == "__main__":
    generate_insights()
