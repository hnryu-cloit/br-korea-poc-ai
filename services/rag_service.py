from __future__ import annotations

import os
from typing import Any, List, Optional

import numpy as np
from sqlalchemy import create_url, create_engine, Column, Integer, String, Text, Index
from sqlalchemy.orm import sessionmaker, declarative_base
from pgvector.sqlalchemy import Vector

from common.logger import init_logger
from common.gemini import Gemini

logger = init_logger("rag_service")
Base = declarative_base()

# -----------------------------------------------------------------------------
# 1. pgvector 데이터 모델 정의
# -----------------------------------------------------------------------------
class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'

    id = Column(Integer, primary_key=True)
    doc_id = Column(String(50), unique=True)
    category = Column(String(50))
    content = Column(Text)
    source = Column(String(100))
    # Gemini text-embedding-004는 768차원 벡터를 생성합니다.
    embedding = Column(Vector(768)) 

class RAGService:
    """
    고도화된 pgvector 기반 RAG 서비스:
    1. SQL과 Vector 데이터를 단일 DB(PostgreSQL)에서 통합 관리합니다.
    2. pgvector의 <-> (L2 Distance) 연산자를 사용하여 시맨틱 검색을 수행합니다.
    3. 데이터 정합성 및 엔터프라이즈 보안 요구사항을 충족합니다.
    """
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        
        # 데이터베이스 연결 설정 (Cloud SQL / AlloyDB URL)
        # POC 환경을 위해 SQLite가 아닌 PostgreSQL 접속을 가정합니다.
        self.db_url = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/br_korea_db")
        
        try:
            self.engine = create_engine(self.db_url)
            self.Session = sessionmaker(bind=self.engine)
            
            # 테이블 및 pgvector 확장 기능 활성화 (최초 1회 실행 권장)
            # self._setup_database()
            
            logger.info("pgvector 기반 RAG 서비스 초기화 완료.")
        except Exception as e:
            logger.error(f"데이터베이스 연결 실패 (pgvector): {e}")
            self.engine = None

    def _setup_database(self):
        """데이터베이스 초기 설정 및 pgvector 확장 활성화"""
        with self.engine.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        Base.metadata.create_all(self.engine)
        logger.info("pgvector 테이블 및 인덱스 설정 완료.")

    def _sync_knowledge_base(self):
        """기존 지식 베이스를 벡터화하여 DB에 동기화 (초기 구축용)"""
        knowledge_base = [
            {"id": "OP_001", "category": "운영", "content": "매장 마감 2시간 전(해피아워)에는 당일 제조 도넛에 대해 20~30% 할인 판매를 실시하여 잔여 재고를 최소화합니다.", "source": "운영 가이드북 v2024"},
            {"id": "OP_002", "category": "운영", "content": "냉동 생지 해동은 실온에서 30-60분간 진행하며, 해동이 완료된 생지는 2시간 이내에 조리해야 품질이 유지됩니다.", "source": "생산 공정 표준서"},
            {"id": "MK_001", "category": "마케팅", "content": "T-day 행사는 매월 첫 번째 수요일입니다. 행사 당일은 평시 대비 유동인구가 1.5배 증가하므로, 전주 대비 25% 이상의 추가 원재료 확보가 권장됩니다.", "source": "2024년 연간 마케팅 플랜"},
            {"id": "MK_002", "category": "마케팅", "content": "배달 앱 프로모션 진행 시, 최소 주문 금액을 15,000원으로 설정하면 객단가가 평균 12% 상승하는 효과가 있습니다.", "source": "배달 채널 분석 리포트"},
            {"id": "CS_001", "category": "고객응대", "content": "제품 품질 불만 접수 시, 즉시 사과 후 제품 교환 또는 환불 처리를 원칙으로 하며 관련 내용은 24시간 이내에 본사에 보고해야 합니다.", "source": "CS 대응 매뉴얼"}
        ]
        
        session = self.Session()
        try:
            for item in knowledge_base:
                # 이미 존재하는지 확인
                exists = session.query(KnowledgeDocument).filter_by(doc_id=item["id"]).first()
                if not exists:
                    # 임베딩 생성 (Gemini)
                    vector = self.gemini.get_embeddings(item["content"])
                    doc = KnowledgeDocument(
                        doc_id=item["id"],
                        category=item["category"],
                        content=item["content"],
                        source=item["source"],
                        embedding=vector
                    )
                    session.add(doc)
            session.commit()
            logger.info("지식 베이스 DB 동기화 완료.")
        except Exception as e:
            session.rollback()
            logger.error(f"DB 동기화 실패: {e}")
        finally:
            session.close()

    def retrieve(self, query: str, top_k: int = 2) -> List[dict[str, str]]:
        """
        pgvector의 벡터 거리 연산(<->)을 사용한 시맨틱 검색
        """
        if not self.engine:
            return []

        # 1. 질문 임베딩 생성
        query_vector = self.gemini.get_embeddings(query)
        
        session = self.Session()
        try:
            # 2. pgvector 거리 연산을 통한 유사 문서 검색
            # <-> 연산자는 L2 Distance를 의미하며, 가장 가까운 문서를 정렬하여 가져옵니다.
            results = session.query(KnowledgeDocument).order_by(
                KnowledgeDocument.embedding.l2_distance(query_vector)
            ).limit(top_k).all()
            
            return [
                {"id": doc.doc_id, "content": doc.content, "source": doc.source, "category": doc.category}
                for doc in results
            ]
        except Exception as e:
            logger.error(f"pgvector 검색 실패: {e}")
            return []
        finally:
            session.close()

    def generate_with_rag(self, prompt: str) -> dict[str, Any]:
        """
        pgvector로 검색된 고정밀 컨텍스트를 사용하여 답변 생성
        """
        docs = self.retrieve(prompt)
        
        if not docs:
            return {"text": None, "sources": None}

        context = "\n".join([f"[{d['source']}] {d['content']}" for d in docs])
        
        rag_prompt = f"""
        당신은 베스킨라빈스/던킨 매장 운영 컨설턴트입니다. 
        아래 PostgreSQL 기반 시맨틱 검색으로 추출된 '참고 문서'를 바탕으로 답변하세요.
        
        [지침]
        1. 반드시 제공된 '참고 문서'의 내용에만 기반하여 정확하게 답변하세요.
        2. 답변 끝에 반드시 참고한 문서의 출처를 명시하세요.
        
        [참고 문서]
        {context}
        
        [점주 질문]
        {prompt}
        """
        
        try:
            logger.info("pgvector 기반 시맨틱 검색 및 Gemini 3.0 Flash 응답 생성 중")
            response = self.gemini.call_gemini_text(rag_prompt, response_type="text")
            return {
                "text": response,
                "sources": list(set([d["source"] for d in docs]))
            }
        except Exception as e:
            logger.error(f"RAG 응답 생성 실패: {e}")
            return {"text": None, "sources": None}
