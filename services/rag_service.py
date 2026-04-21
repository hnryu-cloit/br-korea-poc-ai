"""RAG (Retrieval-Augmented Generation) 서비스.

운영 가이드 / FAQ 기반 근거 검색 + Chunking + Metadata + Rerank 최적화 포함.
"""

from __future__ import annotations

import json
from json import JSONDecodeError
import logging
import os
from pathlib import Path
from typing import Any

from common.gemini import Gemini

logger = logging.getLogger(__name__)

# 최적 청크 설정
CHUNK_SIZE = 512  # 토큰 기준 최적 청크 크기
CHUNK_OVERLAP = 50  # 청크 간 오버랩 토큰 수
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
        """로컬 지식 문서를 읽습니다. 지식 파일이 없으면 빈 목록을 반환합니다."""
        env_path = os.getenv("RAG_KNOWLEDGE_PATH", "").strip()
        candidate_files = []
        if env_path:
            candidate_files.append(Path(env_path))
        candidate_files.append(Path(__file__).resolve().parents[1] / "eval-data" / "knowledge.json")
        documents: list[dict] = []
        for file_path in candidate_files:
            if not file_path.exists():
                continue
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, JSONDecodeError) as exc:
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

        logger.warning("로컬 지식 베이스가 없어 RAG 문서 없이 동작합니다.")
        return []

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
                chunks.append(
                    {
                        "content": chunk_text,
                        "metadata": {
                            "source": source,
                            "section": section,
                            "chunk_index": chunk_idx,
                            "language": "ko",
                        },
                    }
                )
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
            source = (
                " ".join(str(v).lower() for v in metadata.values())
                if isinstance(metadata, dict)
                else ""
            )
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
        store_id: str | None = None,
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
        except (ValueError, TypeError, RuntimeError) as exc:
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
            "retrieved_contexts": [doc.get("content", "") for doc in retrieved],
            "retrieved_count": len(retrieved),
        }

    # ── QA 캐시 ─────────────────────────────────────────────────────────

    def lookup_qa_cache(self, store_id: str, query: str) -> dict | None:
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
