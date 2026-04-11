from __future__ import annotations

import os
import pandas as pd
from typing import Any, List, Optional
from datetime import datetime

import numpy as np
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker, declarative_base
from pgvector.sqlalchemy import Vector

from common.logger import init_logger
from common.gemini import Gemini

logger = init_logger("rag_service")
Base = declarative_base()

# -----------------------------------------------------------------------------
# 1. pgvector 데이터 모델 정의 (비정형 문서 검색용)
# -----------------------------------------------------------------------------
class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'

    id = Column(Integer, primary_key=True)
    doc_id = Column(String(50), unique=True)
    category = Column(String(50))
    content = Column(Text)
    source = Column(String(100))
    
    # [고도화] 메타데이터 필터링을 위한 JSONB 컬럼 추가 (지역, 업태, 캠페인 기간 등 저장)
    metadata_ = Column(JSONB, default=dict)
    
    # Gemini gemini-embedding-001는 3072차원 벡터
    embedding = Column(Vector(3072)) 


class RAGService:
    """
    통합 RAG 서비스 (Vector + Data):
    1. pgvector 기반의 비정형 텍스트(매뉴얼, 운영 가이드 등) 검색 (Vector RAG)
    2. Pandas를 활용한 정형 데이터(매출 엑셀 파일) 로드 및 컨텍스트 생성 (Data RAG)
    """
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        
        # --- 1. pgvector 데이터베이스 설정 ---
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5435/br_korea_poc")
        try:
            self.engine = create_engine(self.db_url)
            self.Session = sessionmaker(bind=self.engine)
            logger.info("pgvector 데이터베이스 연결 준비 완료.")
        except Exception as e:
            logger.error(f"데이터베이스 연결 실패 (pgvector): {e}")
            self.engine = None

        # --- 2. 매출 데이터(Excel) 경로 설정 ---
        # 프로젝트 루트 기준으로 resource 폴더 경로 지정
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.resource_dir = os.path.abspath(os.path.join(current_dir, "../../resource/04. POC 대상 데이터_제공 데이터/02. 매출"))

    # -------------------------------------------------------------------------
    # [Vector RAG] 비정형 지식 베이스 관련 (pgvector)
    # -------------------------------------------------------------------------
    def _setup_database(self):
        """데이터베이스 초기 설정 및 pgvector 확장 활성화"""
        if not self.engine:
            return
        with self.engine.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.create_all(self.engine)
        logger.info("pgvector 테이블 및 인덱스 설정 완료.")

    def retrieve_unstructured(self, query: str, top_k: int = 2, filter_dict: Optional[dict] = None) -> List[dict[str, str]]:
        """
        pgvector의 벡터 거리 연산(<->)을 사용한 시맨틱 검색 + SQL 메타데이터 필터링 (하이브리드)
        """
        if not self.engine:
            logger.warning("DB 엔진이 초기화되지 않아 빈 문서를 반환합니다.")
            return []

        try:
            query_vector = self.gemini.get_embeddings(query)
            
            # [보완] 테스트 환경 등을 위해 임베딩 결과가 없거나 차원이 맞지 않는 경우 처리
            if not query_vector or len(query_vector) == 0:
                logger.warning("임베딩 생성 실패. 0으로 채워진 기본 벡터를 사용합니다.")
                query_vector = [0.0] * 3072
            elif len(query_vector) != 3072:
                # 차원이 다를 경우 3072차원에 맞게 조정
                query_vector = (list(query_vector) + [0.0] * 3072)[:3072]

            session = self.Session()
            # 기본 쿼리 생성
            base_query = session.query(KnowledgeDocument)
            
            # [고도화] 메타데이터 필터링 적용 (Hybrid Search)
            if filter_dict:
                for key, value in filter_dict.items():
                    # JSONB 필터링: KnowledgeDocument.metadata_[key] == value
                    # string 매칭을 위해 .astext 사용
                    base_query = base_query.filter(
                        KnowledgeDocument.metadata_[key].astext == str(value)
                    )
            
            # 벡터 유사도(l2_distance) 기반 정렬 및 가져오기
            results = base_query.order_by(
                KnowledgeDocument.embedding.l2_distance(query_vector)
            ).limit(top_k).all()
            
            return [
                {
                    "id": doc.doc_id, 
                    "content": doc.content, 
                    "source": doc.source, 
                    "category": doc.category,
                    "metadata": doc.metadata_
                }
                for doc in results
            ]
        except Exception as e:
            logger.error(f"pgvector 검색 실패: {e}")
            return []
        finally:
            if 'session' in locals():
                session.close()

    def lookup_qa_cache(self, store_id: str, query: str) -> Optional[dict]:
        """
        벡터 DB에서 특정 매장(store_id)의 매우 유사한 기존 질문-답변 쌍이 있는지 확인합니다.
        l2_distance가 0.1 미만인 경우에만 캐시로 인정합니다.
        """
        if not self.engine:
            return None

        try:
            query_vector = self.gemini.get_embeddings(query)
            if not query_vector: return None

            session = self.Session()
            # 거리(l2_distance)를 함께 조회
            distance_col = KnowledgeDocument.embedding.l2_distance(query_vector)
            
            result = session.query(KnowledgeDocument, distance_col).filter(
                KnowledgeDocument.category == "qa_cache",
                KnowledgeDocument.metadata_["store_id"].astext == str(store_id)
            ).order_by(distance_col).first()

            if result:
                doc, distance = result
                # 임계치(Threshold) 설정: 0.1 미만일 때만 '거의 동일한 질문'으로 판단
                if distance < 0.1:
                    logger.info(f"🎉 캐시 적중! (거리: {distance:.4f}) 매칭 질문: {doc.source}")
                    return doc.metadata_.get("cached_result")
                else:
                    logger.info(f"⚠️ 유사 질문 발견되었으나 거리가 멂 (거리: {distance:.4f}). 새로 분석합니다.")
                
            return None
        except Exception as e:
            logger.error(f"QA 캐시 조회 중 오류: {e}")
            return None
        finally:
            if 'session' in locals(): session.close()

    def save_qa_cache(self, store_id: str, query: str, result_dict: dict):
        """
        특정 매장의 질문-답변 쌍을 벡터 DB 캐시에 저장합니다.
        """
        if not self.engine: return

        try:
            query_vector = self.gemini.get_embeddings(query)
            if not query_vector: return

            session = self.Session()
            new_cache = KnowledgeDocument(
                doc_id=f"cache_{store_id}_{hash(query)}",
                category="qa_cache",
                content=query,
                source=query,
                # [수정] 메타데이터에 store_id를 명시적으로 저장
                metadata_={
                    "store_id": store_id, 
                    "cached_result": result_dict, 
                    "saved_at": str(datetime.now())
                },
                embedding=query_vector
            )
            session.add(new_cache)
            session.commit()
            logger.info(f"매장 {store_id}의 새로운 결과가 캐시에 저장되었습니다.")
        except Exception as e:
            logger.error(f"QA 캐시 저장 중 오류: {e}")
        finally:
            if 'session' in locals(): session.close()

    def retrieve_store_profile(self, store_id: str) -> str:
        """
        벡터 DB에서 해당 매장의 고유한 특성(상권 정보, 날씨 영향도, 유동인구 등)을 검색합니다.
        category='store_profile'인 문서를 검색합니다.
        """
        if not self.engine:
            return "매장 프로필 정보가 데이터베이스에 존재하지 않습니다."

        session = self.Session()
        try:
            # store_id가 포함된 매장 프로필 문서 검색
            # (실제 구현에서는 store_id 필드를 추가하거나, doc_id를 store_id로 설정 가능)
            doc = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.category == "store_profile",
                KnowledgeDocument.doc_id == store_id
            ).first()
            
            if doc:
                logger.info(f"매장 프로필 검색 성공 (Store ID: {store_id})")
                return doc.content
            
            # 직접 매칭되는 문서가 없을 경우, 유사도 기반 검색 시도 (쿼리: 'store_id 매장 특징')
            query = f"{store_id} 점포 매장 특성 및 상권 정보"
            results = self.retrieve_unstructured(query, top_k=1)
            
            if results:
                return results[0]['content']
                
            return f"해당 매장({store_id})에 대해 등록된 특성 정보가 없습니다."
        except Exception as e:
            logger.error(f"매장 프로필 검색 중 오류: {e}")
            return "매장 프로필을 불러오는 중 오류가 발생했습니다."
        finally:
            session.close()

    # -------------------------------------------------------------------------
    # [Data RAG] 정형 매출 데이터 관련 (Pandas & Excel)
    # -------------------------------------------------------------------------
    def _load_actual_data(self, data_type: str) -> pd.DataFrame:
        """
        Semantic Layer에서 판별한 data_type에 따라 실제 제공된 Excel 파일을 로드합니다.
        *주의: POC 단계이므로 LLM 토큰 제한 및 응답 속도를 위해 상위 50행만 샘플링합니다.
        실제 환경에서는 DB 쿼리를 통한 Aggregation(집계)을 권장합니다.
        """
        try:
            file_path = ""
            if data_type == "payment":
                file_path = os.path.join(self.resource_dir, "03. 일자별 결제 수단별 매출 데이터/일자별+결제+수단별+매출.xlsx")
            elif data_type == "hourly":
                file_path = os.path.join(self.resource_dir, "01. 일자별 시간대별 상품별 매출 데이터/일자별+시간대별+상품별+매출_01.xlsx")
            elif data_type == "channel":
                # 오프라인 데이터 로드 (온라인 파일도 존재하나 병합 복잡도로 인해 우선 하나만 로드)
                file_path = os.path.join(self.resource_dir, "05. 일자별 온_오프라인 구분/일자별+오프라인.xlsx")
            elif data_type == "campaign":
                file_path = os.path.join(self.resource_dir, "02. 일자별 시간대별 캠페인 매출 데이터/일자별+시간대별+캠페인+매출.xlsx")
            else: # general_sales
                file_path = os.path.join(self.resource_dir, "04. 일자별 상품별 매출/일자별+상품별+매출.xlsx")

            if not os.path.exists(file_path):
                logger.warning(f"경로에 파일이 없습니다: {file_path}")
                return pd.DataFrame({"알림": ["해당 데이터 소스를 찾을 수 없습니다."]})

            logger.info(f"데이터 파일 로드 중... (nrows=50 제한 적용): {os.path.basename(file_path)}")
            # 용량이 매우 큰 파일들(60MB+)이므로 nrows=50 설정하여 POC 응답 속도 최적화
            df = pd.read_excel(file_path, nrows=50)
            return df

        except Exception as e:
            logger.error(f"엑셀 데이터 로딩 중 오류 발생: {e}")
            return pd.DataFrame({"Error": [f"데이터 로딩 실패: {str(e)}"]})

    def retrieve_sales_context(self, target_data_type: str) -> str:
        """
        분류된 타겟 데이터 타입에 맞춰 DataFrame을 로드하고, 
        LLM이 이해하기 쉬운 마크다운(Markdown) 표 형태로 변환합니다.
        POC 시나리오별로 특화된 비교 데이터(전년, 동일상권, 가맹점평균 등)를 모의(Mock)로 제공합니다.
        """
        try:
            # 1. 시나리오별 특화 데이터 생성 (POC용 하드코딩 데이터 추가)
            context_str = f"### [데이터 소스: {target_data_type} 매출 샘플 데이터]\n"
            
            if target_data_type == "general_sales":
                context_str += """
| 비교 항목 | 내 점포 (26년 2월) | 전년 동월 (25년 2월) | 테스트 가맹점 10곳 평균 |
| :--- | :--- | :--- | :--- |
| 총 매출 | 45,000,000원 | 42,000,000원 | 40,500,000원 |
| 미니도넛 세트 | 2,100,000원 | 1,800,000원 | 1,950,000원 |
| 글레이즈드(811047) | 4,500,000원 | 4,650,000원 | 4,200,000원 |
| 평일 평균 매출 | 1,400,000원 | 1,300,000원 | 1,250,000원 |
| 주말 평균 매출 | 2,100,000원 | 1,950,000원 | 1,850,000원 |
"""
            elif target_data_type == "channel":
                context_str += """
| 구분 | 내 점포 (이번 달) | 유사 상권 평균 (이번 달) | 전월 (내 점포) |
| :--- | :--- | :--- | :--- |
| 전체 배달 건수 | 450건 | 580건 | 410건 |
| 배민 매출 비중 | 55% | 40% | 58% |
| 쿠팡이츠 매출 비중 | 25% | 35% | 20% |
| 해피오더 매출 비중 | 20% | 25% | 22% |
| 배달 총 매출 | 12,000,000원 | 15,500,000원 | 11,200,000원 |
"""
            elif target_data_type == "campaign":
                context_str += """
| 행사 구분 | 이번 T-Day (26년 2월) | 이전 T-Day (26년 1월) | 동일 상권 평균 (이번 T-Day) |
| :--- | :--- | :--- | :--- |
| T-Day 총 매출 | 3,200,000원 | 2,800,000원 | 3,500,000원 |
| 결제 건수 (객수) | 350건 | 310건 | 380건 |
| 주요 품목 재고 소진율| 95% (조기 품절 2건) | 88% | 85% |
| 행사 할인액 | 800,000원 | 700,000원 | 880,000원 |
"""
            elif target_data_type == "payment":
                context_str += """
| 결제 수단 | 결제 건수 | 매출 금액 | 전월 대비 증감 |
| :--- | :--- | :--- | :--- |
| 신용카드 | 1,200건 | 25,000,000원 | +5% |
| 간편결제(페이) | 850건 | 15,000,000원 | +12% |
| 현금 | 150건 | 2,000,000원 | -3% |
| 제휴할인/쿠폰 | 400건 | 3,000,000원 | +8% |
"""
            else:
                # 기본 파일 로드
                df = self._load_actual_data(target_data_type)
                context_str += df.to_markdown(index=False)
            
            return context_str
        except Exception as e:
            logger.error(f"데이터 컨텍스트 변환 실패: {e}")
            return "조회된 매출 데이터가 없습니다."
