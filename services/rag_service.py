"""RAG (Retrieval-Augmented Generation) 서비스.

운영 가이드 / FAQ 기반 근거 검색 + Chunking + Metadata + Rerank 최적화 포함.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from common.gemini import Gemini

logger = logging.getLogger(__name__)

# 최적 청크 설정
CHUNK_SIZE = 512      # 토큰 기준 최적 청크 크기
CHUNK_OVERLAP = 50    # 청크 간 오버랩 토큰 수
_DEFAULT_KNOWLEDGE_DOCS: list[dict[str, str]] = [
    {
        "source": "operations_guide",
        "section": "ordering_deadline",
        "content": "주문 관련 운영 가이드입니다. 주문 마감 20분 전에는 점주에게 주문 검토 알림을 제공하고, 추천 옵션은 최종 결정이 아닌 보조 자료로 안내해야 합니다.",
    },
    {
        "source": "operations_guide",
        "section": "production_risk",
        "content": "생산 관리 운영 가이드입니다. 품절 위험 SKU는 1시간 후 예측 재고와 최근 판매 속도를 함께 보고 즉시 생산, 주의, 정상 상태로 구분합니다.",
    },
    {
        "source": "operations_guide",
        "section": "sales_faq",
        "content": "매출 분석 FAQ입니다. 배달, 채널, 비교, 수익성 질의에는 실제 데이터 근거와 실행 가능한 액션을 함께 제시하고 민감정보는 차단해야 합니다.",
    },
    {
        "source": "faq",
        "section": "guardrail",
        "content": "점주는 임의 가격 변경이나 비공식 할인 정책을 수행할 수 없습니다. 발주, 진열, 배달앱 운영, 리뷰 관리 범위의 액션만 제안해야 합니다.",
    },
]


class RAGService:
    """지식 베이스 기반 RAG 서비스.

    - 512 토큰 청크 + 50 토큰 오버랩으로 문서 분할
    - 메타데이터 (source, section, language) 부착
    - 키워드 기반 relevance reranking
    """

    def __init__(self, gemini_client: Gemini, knowledge_base: list[dict] | None = None) -> None:
        self.gemini = gemini_client
        self._knowledge_base: list[dict] = knowledge_base or self._load_default_knowledge_base()
        self._chunks: list[dict] = []
        self._qa_cache: dict[str, Any] = {}
        if self._knowledge_base:
            self._chunks = self._build_chunks(self._knowledge_base)

    def _load_default_knowledge_base(self) -> list[dict]:
        """로컬 지식 문서를 읽거나 내장 문서를 fallback으로 사용합니다."""
        candidate_files = [
            Path(__file__).resolve().parents[1] / "eval-data" / "sample.json",
        ]
        documents: list[dict] = []
        for file_path in candidate_files:
            if not file_path.exists():
                continue
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("기본 지식 파일 로드 실패: %s (%s)", file_path, exc)
                continue
            if isinstance(payload, list):
                documents.extend(item for item in payload if isinstance(item, dict))
            elif isinstance(payload, dict):
                items = payload.get("documents") or payload.get("items")
                if isinstance(items, list):
                    documents.extend(item for item in items if isinstance(item, dict))

        if documents:
            logger.info("로컬 지식 베이스 문서 %d건 로드", len(documents))
            return documents

        logger.info("로컬 지식 베이스가 없어 내장 문서를 사용합니다.")
        return list(_DEFAULT_KNOWLEDGE_DOCS)

    # ── 문서 청킹 ────────────────────────────────────────────────────────

    def _build_chunks(self, documents: list[dict]) -> list[dict]:
        """문서 리스트를 CHUNK_SIZE 크기로 분할하고 메타데이터를 부착합니다."""
        chunks: list[dict] = []
        for doc_idx, doc in enumerate(documents):
            content: str = doc.get("content", doc.get("text", ""))
            source: str = doc.get("source", "operations_guide")
            section: str = doc.get("section", str(doc_idx))
            words = content.split()
            start = 0
            chunk_idx = 0
            while start < len(words):
                end = min(start + CHUNK_SIZE, len(words))
                chunk_text = " ".join(words[start:end])
                chunks.append({
                    "content": chunk_text,
                    "metadata": {
                        "source": source,
                        "section": section,
                        "chunk_index": chunk_idx,
                        "language": "ko",
                    },
                })
                chunk_idx += 1
                if end >= len(words):
                    break
                start = end - CHUNK_OVERLAP  # 오버랩 적용
        logger.info("RAG 청크 생성 완료: %d docs → %d chunks", len(documents), len(chunks))
        return chunks

    def load_documents(self, documents: list[dict]) -> None:
        """외부에서 문서를 주입하고 청킹을 재실행합니다."""
        self._knowledge_base = documents
        self._chunks = self._build_chunks(documents)

    # ── Relevance Reranking ──────────────────────────────────────────────

    def rerank_by_relevance(self, query: str, docs: list[dict]) -> list[dict]:
        """키워드 오버랩 기반으로 문서를 관련도 순으로 정렬합니다."""
        query_words = {
            token
            for token in query.lower().replace("/", " ").replace(",", " ").split()
            if len(token) >= 2
        }
        scored: list[tuple[int, dict]] = []
        for doc in docs:
            content = doc.get("content", doc.get("text", "")).lower()
            metadata = doc.get("metadata", {})
            source = " ".join(str(v).lower() for v in metadata.values()) if isinstance(metadata, dict) else ""
            haystack = f"{content} {source}"
            score = sum(2 if w in content else 1 for w in query_words if w in haystack)
            scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored]

    # ── 검색 ────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """질의와 관련된 상위 top_k 청크를 반환합니다."""
        if not self._chunks:
            logger.warning("지식 베이스가 비어 있습니다.")
            return []
        ranked = self.rerank_by_relevance(query, self._chunks)
        return ranked[:top_k]

    # ── RAG 응답 생성 ───────────────────────────────────────────────────

    def generate_with_rag(
        self,
        query: str,
        store_id: Optional[str] = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """RAG 파이프라인: 검색 → 컨텍스트 구성 → LLM 응답 생성."""
        retrieved = self.retrieve(query, top_k=top_k)
        context_text = "\n\n".join(
            f"[출처: {doc['metadata']['source']} / {doc['metadata']['section']}]\n{doc['content']}"
            for doc in retrieved
        )

        if not context_text:
            context_text = "관련 운영 가이드를 찾을 수 없습니다."

        prompt = (
            f"다음 운영 가이드 내용을 참고하여 질문에 답변하세요.\n\n"
            f"[운영 가이드]\n{context_text}\n\n"
            f"[질문] {query}\n\n"
            f"간결하고 실행 가능한 답변을 한국어로 작성하세요."
        )

        try:
            answer = self.gemini.call_gemini_text(prompt)
        except Exception as exc:
            logger.error("RAG Gemini 호출 실패: %s", exc)
            answer = "운영 가이드 기반 답변을 생성할 수 없습니다. 잠시 후 다시 시도해주세요."

        source_objects = [
            {
                "source": doc["metadata"]["source"],
                "section": doc["metadata"]["section"],
            }
            for doc in retrieved
        ]
        sources = [f"{item['source']}:{item['section']}" for item in source_objects]

        return {
            "text": answer,
            "answer": answer,
            "sources": sources,
            "source_documents": source_objects,
            "retrieved_count": len(retrieved),
        }

    # ── QA 캐시 ─────────────────────────────────────────────────────────

    def lookup_qa_cache(self, store_id: str, query: str) -> Optional[dict]:
        """store_id + query 기반 QA 캐시 조회"""
        key = f"{store_id}:{query.strip()[:80]}"
        cached = self._qa_cache.get(key)
        if cached:
            logger.debug("QA 캐시 적중: store=%s", store_id)
        return cached

    def save_qa_cache(self, store_id: str, query: str, result: dict) -> None:
        """분석 결과를 QA 캐시에 저장"""
        key = f"{store_id}:{query.strip()[:80]}"
        self._qa_cache[key] = result
        logger.info("QA 캐시 저장: store=%s key_len=%d", store_id, len(key))

    def retrieve_store_profile(self, store_id: str) -> str:
        """매장 ID 기반 운영 프로필 문자열 반환.

        지식 베이스에 매장별 문서가 있으면 상위 결과를 반환하고,
        없으면 범용 안내 메시지를 반환한다.
        """
        results = self.retrieve(query=f"매장 {store_id} 운영 특성", top_k=3)
        if results:
            return "\n".join(doc.get("content", "")[:200] for doc in results)
        return (
            f"매장 {store_id}에 대한 사전 프로필 정보가 없습니다. "
            "실시간 DB 분석 결과를 우선합니다."
        )
