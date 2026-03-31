from __future__ import annotations

import numpy as np
import faiss
from typing import Any, List, Optional

from common.logger import init_logger
from common.gemini import Gemini

logger = init_logger("rag_service")

class RAGService:
    """
    고도화된 RAG 서비스 (MVP 수준):
    1. Gemini Embedding 모델을 사용하여 지식을 벡터화합니다.
    2. FAISS(Facebook AI Similarity Search) 엔진을 통해 시맨틱 검색을 수행합니다.
    3. Gemini 3.0 Flash로 최종 답변을 생성합니다.
    """
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.dimension = 768  # Gemini text-embedding-004 차원수
        self.index = faiss.IndexFlatL2(self.dimension)
        
        # 지식 베이스
        self.knowledge_base = [
            {"id": "OP_001", "content": "매장 마감 2시간 전(해피아워)에는 당일 제조 도넛에 대해 20~30% 할인 판매를 실시하여 잔여 재고를 최소화합니다.", "source": "운영 가이드북 v2024"},
            {"id": "OP_002", "content": "냉동 생지 해동은 실온에서 30-60분간 진행하며, 해동이 완료된 생지는 2시간 이내에 조리해야 품질이 유지됩니다.", "source": "생산 공정 표준서"},
            {"id": "MK_001", "content": "T-day 행사는 매월 첫 번째 수요일입니다. 행사 당일은 평시 대비 유동인구가 1.5배 증가하므로, 전주 대비 25% 이상의 추가 원재료 확보가 권장됩니다.", "source": "2024년 연간 마케팅 플랜"},
            {"id": "MK_002", "content": "배달 앱 프로모션 진행 시, 최소 주문 금액을 15,000원으로 설정하면 객단가가 평균 12% 상승하는 효과가 있습니다.", "source": "배달 채널 분석 리포트"},
            {"id": "CS_001", "content": "제품 품질 불만 접수 시, 즉시 사과 후 제품 교환 또는 환불 처리를 원칙으로 하며 관련 내용은 24시간 이내에 본사에 보고해야 합니다.", "source": "CS 대응 매뉴얼"}
        ]
        
        # 지식 베이스 벡터화 (Indexing)
        self._build_index()

    def _build_index(self):
        """지식 베이스의 모든 문서를 임베딩하여 FAISS 인덱스에 추가합니다."""
        logger.info("RAG 지식 베이스 인덱싱 시작...")
        embeddings = []
        for doc in self.knowledge_base:
            # 각 문서의 내용을 벡터로 변환 (Gemini Embedding 모델 호출)
            vector = self.gemini.get_embeddings(doc["content"])
            embeddings.append(vector)
            
        # FAISS 인덱스에 벡터 등록
        embedding_array = np.array(embeddings).astype('float32')
        self.index.add(embedding_array)
        logger.info(f"RAG 인덱싱 완료: 총 {len(self.knowledge_base)}개 문서 등록됨.")

    def retrieve(self, query: str, top_k: int = 2) -> List[dict[str, str]]:
        """
        벡터 유사도 기반 시맨틱 검색 수행
        """
        # 질문을 벡터로 변환
        query_vector = self.gemini.get_embeddings(query)
        query_array = np.array([query_vector]).astype('float32')
        
        # 가장 유사한 문서 검색 (L2 Distance 기반)
        distances, indices = self.index.search(query_array, top_k)
        
        results = []
        for idx in indices[0]:
            if idx != -1:  # 유효한 인덱스인 경우
                results.append(self.knowledge_base[idx])
        
        return results

    def generate_with_rag(self, prompt: str) -> dict[str, Any]:
        """
        검색된 문서(Semantic Context)를 기반으로 답변을 생성합니다.
        """
        # 1. 시맨틱 검색 (가장 유사한 문서 추출)
        docs = self.retrieve(prompt)
        
        if not docs:
            return {"text": None, "sources": None}

        # 2. 컨텍스트 구성 및 프롬프트 생성
        context = "\n".join([f"[{d['source']}] {d['content']}" for d in docs])
        
        rag_prompt = f"""
        당신은 베스킨라빈스/던킨 매장 운영 컨설턴트입니다. 아래 제공된 '참고 문서'를 바탕으로 점주의 질문에 전문적이고 친절하게 답변하세요.
        
        [지침]
        1. 제공된 '참고 문서'의 내용에 절대적으로 기반하여 답변하세요.
        2. 질문의 의미가 문서 내용과 유사할 경우 적극적으로 활용하세요.
        3. 답변 끝에 반드시 참고한 문서의 출처를 명시하세요.
        
        [참고 문서]
        {context}
        
        [점주 질문]
        {prompt}
        """
        
        try:
            logger.info("시맨틱 검색 기반 응답 생성 중 (Gemini 3.0 Flash)")
            response = self.gemini.call_gemini_text(rag_prompt, response_type="text")
            return {
                "text": response,
                "sources": list(set([d["source"] for d in docs]))
            }
        except Exception as e:
            logger.error(f"RAG 응답 생성 실패: {e}")
            return {"text": None, "sources": None}
