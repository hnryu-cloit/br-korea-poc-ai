import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Load env
load_dotenv()

# Add AI project root to path to import common modules
AI_ROOT = Path(__file__).resolve().parents[1]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

try:
    from common.gemini import Gemini
    from pipeline.db_models import Base, KnowledgeDocument
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5435/br_korea_poc")
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("pgvector DB schema initialized with metadata JSONB column.")


def build_store_vectors(gemini_client, session, data_dir):
    print("Building Store Master Vectors...")
    file_path = os.path.join(data_dir, "STOR_MST.xlsx")
    if not os.path.exists(file_path):
        print("STOR_MST.xlsx not found.")
        return

    df = pd.read_excel(file_path)
    cols = df.columns.tolist()

    docs = []
    for idx, row in df.iterrows():
        try:
            store_cd = str(row.get("MASKED_STOR_CD", row.iloc[1] if len(cols) > 1 else ""))
            store_nm = str(row.get("MAKED_STOR_NM", row.iloc[2] if len(cols) > 2 else ""))

            store_type = str(row.iloc[5]) if len(cols) > 5 else ""
            biz_type = str(row.iloc[6]) if len(cols) > 6 else ""
            region = str(row.iloc[8]) if len(cols) > 8 else ""

            content = f"점포코드 {store_cd}({store_nm}) 상권 및 특성 정보입니다. "
            if region and region != "nan":
                content += f"이 매장은 {region} 상권에 위치하고 있습니다. "
            if store_type and store_type != "nan":
                content += f"매장 형태는 {store_type}이며, "
            if biz_type and biz_type != "nan":
                content += f"업태는 {biz_type}입니다. "

            docs.append(
                {
                    "doc_id": f"STORE_{store_cd}",
                    "category": "Store",
                    "content": content,
                    "source": "STOR_MST.xlsx",
                    "metadata": {
                        "store_cd": store_cd,
                        "store_nm": store_nm,
                        "region": region if region != "nan" else None,
                        "store_type": store_type if store_type != "nan" else None,
                        "biz_type": biz_type if biz_type != "nan" else None,
                    },
                }
            )
        except Exception:
            pass

    embed_and_insert(gemini_client, session, docs)


def build_campaign_vectors(gemini_client, session, data_dir):
    print("Building Campaign Master Vectors...")
    file_path = os.path.join(data_dir, "CPI_MST.xlsx")
    if not os.path.exists(file_path):
        print("CPI_MST.xlsx not found.")
        return

    df = pd.read_excel(file_path)
    docs = []
    for idx, row in df.iterrows():
        try:
            cpi_cd = str(row.get("CPI_CD", row.iloc[0]))
            cpi_nm = str(row.get("CPI_NM", row.iloc[1] if len(row) > 1 else ""))
            start_dt = str(row.get("START_DT", ""))
            end_dt = str(row.get("END_DT", ""))

            content = f"캠페인(행사) 코드 {cpi_cd}의 이름은 '{cpi_nm}' 입니다. "
            if start_dt and end_dt:
                content += f"해당 행사는 {start_dt}부터 {end_dt}까지 진행됩니다. "

            content += "매출 분석 시 해당 기간 동안의 캠페인 효과를 고려해야 합니다."

            docs.append(
                {
                    "doc_id": f"CAMPAIGN_{cpi_cd}",
                    "category": "Campaign",
                    "content": content,
                    "source": "CPI_MST.xlsx",
                    "metadata": {
                        "campaign_cd": cpi_cd,
                        "campaign_nm": cpi_nm,
                        "start_dt": start_dt,
                        "end_dt": end_dt,
                    },
                }
            )
        except Exception:
            pass

    embed_and_insert(gemini_client, session, docs)


def build_payment_vectors(gemini_client, session, data_dir):
    print("Building Payment Code Vectors...")
    file_path = os.path.join(data_dir, "PAY_CD.csv")
    if not os.path.exists(file_path):
        print("PAY_CD.csv not found.")
        return

    try:
        df = pd.read_csv(file_path, encoding="euc-kr")
    except:
        df = pd.read_csv(file_path, encoding="utf-8")

    docs = []
    for idx, row in df.iterrows():
        try:
            pay_cd = str(row.get("PAY_WAY_CD", row.iloc[0]))
            pay_nm = str(row.get("PAY_WAY_NM", row.iloc[1] if len(row) > 1 else ""))

            content = f"결제수단코드 '{pay_cd}'는 '{pay_nm}'를 의미합니다. 고객이 {pay_nm}로 결제한 내역을 분석할 때 참고합니다."
            docs.append(
                {
                    "doc_id": f"PAY_{pay_cd}_{idx}",
                    "category": "Payment",
                    "content": content,
                    "source": "PAY_CD.csv",
                    "metadata": {"pay_cd": pay_cd, "pay_nm": pay_nm},
                }
            )
        except Exception:
            pass

    embed_and_insert(gemini_client, session, docs)


def embed_and_insert(gemini_client, session, docs):
    print(f"Embedding and inserting {len(docs)} documents...")
    inserted = 0
    for doc in docs:
        existing = session.query(KnowledgeDocument).filter_by(doc_id=doc["doc_id"]).first()
        if existing:
            continue

        try:
            embedding = gemini_client.get_embeddings(doc["content"], model="gemini-embedding-001")

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

            if inserted % 50 == 0:
                session.commit()
                print(f"  Inserted {inserted} docs...")
        except Exception as e:
            print(f"  Error embedding doc {doc['doc_id']}: {e}")
            session.rollback()

    session.commit()
    print(f"Finished inserting {inserted} new documents.")


def main():
    gemini_client = Gemini()
    init_db()
    session = SessionLocal()

    # 리소스 폴더 후보 경로를 순서대로 탐색
    data_candidates = [
        AI_ROOT / "resources" / "data",
        AI_ROOT.parent / "resource",
        AI_ROOT.parent / "resources",
    ]
    data_dir = next((str(path) for path in data_candidates if path.exists()), str(data_candidates[0]))
    print(f"Using data_dir: {data_dir}")

    build_store_vectors(gemini_client, session, data_dir)
    build_campaign_vectors(gemini_client, session, data_dir)
    build_payment_vectors(gemini_client, session, data_dir)

    session.close()
    print("Vector DB Build Complete!")


if __name__ == "__main__":
    main()
