"""RAG (Retrieval-Augmented Generation) 서비스.

운영 가이드 / FAQ 기반 근거 검색 + Chunking + Metadata + Rerank 최적화 포함.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from common.gemini import Gemini

logger = logging.getLogger(__name__)

# 최적 청크 설정
CHUNK_SIZE = 512      # 토큰 기준 최적 청크 크기
CHUNK_OVERLAP = 50    # 청크 간 오버랩 토큰 수


class RAGService:
    """지식 베이스 기반 RAG 서비스.

    - 512 토큰 청크 + 50 토큰 오버랩으로 문서 분할
    - 메타데이터 (source, section, language) 부착
    - 키워드 기반 relevance reranking
    """

    def __init__(self, gemini_client: Gemini, knowledge_base: list[dict] | None = None) -> None:
        self.gemini = gemini_client
        self._knowledge_base: list[dict] = knowledge_base or []
        self._chunks: list[dict] = []
        if self._knowledge_base:
            self._chunks = self._build_chunks(self._knowledge_base)

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
        query_words = set(query.lower().split())
        scored: list[tuple[int, dict]] = []
        for doc in docs:
            content = doc.get("content", doc.get("text", "")).lower()
            score = sum(1 for w in query_words if w in content)
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

        sources = [
            {
                "source": doc["metadata"]["source"],
                "section": doc["metadata"]["section"],
            }
            for doc in retrieved
        ]

        return {
            "answer": answer,
            "sources": sources,
            "retrieved_count": len(retrieved),
        }