from __future__ import annotations

import json
import logging
from typing import Any

from common.gemini import Gemini
from services.rag_service import RAGService

logger = logging.getLogger(__name__)


class OrderingHistoryInsightService:
    """주문 이력 이상징후 RAG+LLM 인사이트 생성"""

    def __init__(self, gemini_client: Gemini, rag_service: RAGService) -> None:
        self.gemini = gemini_client
        self.rag_service = rag_service

    def generate(
        self,
        *,
        store_id: str,
        filters: dict[str, Any],
        history_items: list[dict[str, Any]],
        summary_stats: dict[str, Any],
    ) -> dict[str, Any]:
        retrieved_guides = self.rag_service.retrieve(
            query=self._build_retrieval_query(store_id=store_id, filters=filters, summary_stats=summary_stats),
            top_k=4,
        )
        history_contexts = self._build_history_contexts(history_items)
        prompt = self._build_prompt(
            store_id=store_id,
            filters=filters,
            summary_stats=summary_stats,
            retrieved_guides=retrieved_guides,
            history_contexts=history_contexts,
        )
        try:
            raw = self.gemini.call_gemini_text(prompt, response_type="application/json")
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            logger.exception("주문 이력 인사이트 생성 실패")
            raise RuntimeError("ordering history insight generation failed") from exc

        if not isinstance(parsed, dict):
            raise ValueError("ordering history insight response must be object")
        return self._normalize_response(
            raw=parsed,
            retrieved_guides=retrieved_guides,
            history_contexts=history_contexts,
        )

    @staticmethod
    def _build_retrieval_query(
        *,
        store_id: str,
        filters: dict[str, Any],
        summary_stats: dict[str, Any],
    ) -> str:
        date_from = str(filters.get("date_from") or "")
        date_to = str(filters.get("date_to") or "")
        return (
            f"매장 {store_id} 발주 이력 이상징후 분석 가이드 "
            f"기간 {date_from}~{date_to} "
            f"자동비율 {summary_stats.get('auto_rate')} 수동비율 {summary_stats.get('manual_rate')}"
        )

    @staticmethod
    def _build_history_contexts(history_items: list[dict[str, Any]], top_k: int = 6) -> list[str]:
        scored_items: list[tuple[float, str]] = []
        for item in history_items:
            item_nm = str(item.get("item_nm") or "").strip()
            if not item_nm:
                continue
            ord_qty = OrderingHistoryInsightService._to_float(item.get("ord_qty"), default=0.0)
            confrm_qty = OrderingHistoryInsightService._to_float(item.get("confrm_qty"), default=0.0)
            gap_ratio = abs(confrm_qty - ord_qty) / max(ord_qty, 1.0)
            is_auto = bool(item.get("is_auto"))
            score = gap_ratio + (0.1 if not is_auto else 0.0)
            context = (
                f"품목={item_nm}, 납품일={item.get('dlv_dt')}, "
                f"발주={int(round(ord_qty))}, 확정={int(round(confrm_qty))}, "
                f"방식={'자동' if is_auto else '수동'}, 차이율={round(gap_ratio, 3)}"
            )
            scored_items.append((score, context))

        scored_items.sort(key=lambda row: row[0], reverse=True)
        return [context for _, context in scored_items[:top_k]]

    @staticmethod
    def _build_prompt(
        *,
        store_id: str,
        filters: dict[str, Any],
        summary_stats: dict[str, Any],
        retrieved_guides: list[dict[str, Any]],
        history_contexts: list[str],
    ) -> str:
        guide_contexts = []
        for doc in retrieved_guides:
            metadata = doc.get("metadata") or {}
            source = metadata.get("source", "operations_guide")
            section = metadata.get("section", "unknown")
            guide_contexts.append(f"[{source}:{section}] {doc.get('content', '')}")

        payload = {
            "store_id": store_id,
            "filters": filters,
            "summary_stats": summary_stats,
            "history_contexts": history_contexts,
        }

        return f"""
당신은 던킨 매장 주문이력 이상징후 분석가입니다.
반드시 아래 입력 데이터와 RAG 컨텍스트만 사용해 JSON만 생성하세요.

[RAG 운영가이드]
{chr(10).join(guide_contexts) if guide_contexts else "운영 가이드 문서 없음"}

[주문이력 컨텍스트]
{chr(10).join(history_contexts) if history_contexts else "주문이력 컨텍스트 없음"}

[입력 데이터 JSON]
{json.dumps(payload, ensure_ascii=False)}

규칙:
1) 근거 없는 수치/품목 생성 금지
2) anomalies는 최대 8개, top_changed_items는 최대 5개
3) confidence는 0~1
4) sources에는 반드시 운영가이드 source와 주문이력 출처를 포함
5) 아래 스키마의 필드명/타입을 정확히 지킬 것

JSON 스키마:
{{
  "kpis": [{{"key":"string","label":"string","value":"string","tone":"default|primary|warning|danger|success"}}],
  "anomalies": [{{"id":"string","severity":"low|medium|high","kind":"string","message":"string","recommended_action":"string","related_items":["string"]}}],
  "top_changed_items": [{{"item_nm":"string","avg_ord_qty":0.0,"latest_ord_qty":0,"change_ratio":0.0}}],
  "sources": ["string"],
  "retrieved_contexts": ["string"],
  "confidence": 0.0
}}
""".strip()

    @staticmethod
    def _normalize_response(
        *,
        raw: dict[str, Any],
        retrieved_guides: list[dict[str, Any]],
        history_contexts: list[str],
    ) -> dict[str, Any]:
        raw_kpis = raw.get("kpis")
        raw_anomalies = raw.get("anomalies")
        raw_changed = raw.get("top_changed_items")
        if not isinstance(raw_kpis, list) or not isinstance(raw_anomalies, list) or not isinstance(raw_changed, list):
            raise ValueError("required list fields are missing")

        kpis = []
        for row in raw_kpis[:6]:
            if not isinstance(row, dict):
                continue
            kpis.append(
                {
                    "key": str(row.get("key") or ""),
                    "label": str(row.get("label") or ""),
                    "value": str(row.get("value") or ""),
                    "tone": OrderingHistoryInsightService._normalize_tone(row.get("tone")),
                }
            )

        anomalies = []
        for idx, row in enumerate(raw_anomalies[:8], start=1):
            if not isinstance(row, dict):
                continue
            related_items = row.get("related_items")
            anomalies.append(
                {
                    "id": str(row.get("id") or f"anomaly-{idx}"),
                    "severity": OrderingHistoryInsightService._normalize_severity(row.get("severity")),
                    "kind": str(row.get("kind") or "이상 징후"),
                    "message": str(row.get("message") or ""),
                    "recommended_action": str(row.get("recommended_action") or ""),
                    "related_items": [str(item) for item in related_items] if isinstance(related_items, list) else [],
                }
            )

        changed_items = []
        for row in raw_changed[:5]:
            if not isinstance(row, dict):
                continue
            changed_items.append(
                {
                    "item_nm": str(row.get("item_nm") or ""),
                    "avg_ord_qty": round(
                        OrderingHistoryInsightService._to_float(row.get("avg_ord_qty"), default=0.0),
                        2,
                    ),
                    "latest_ord_qty": int(
                        round(OrderingHistoryInsightService._to_float(row.get("latest_ord_qty"), default=0.0))
                    ),
                    "change_ratio": round(
                        OrderingHistoryInsightService._to_float(row.get("change_ratio"), default=0.0),
                        4,
                    ),
                }
            )

        raw_sources = raw.get("sources")
        sources: list[str] = []
        if isinstance(raw_sources, list):
            sources.extend(str(source) for source in raw_sources if source)
        if retrieved_guides:
            sources.extend(
                f"{doc.get('metadata', {}).get('source', 'operations_guide')}:{doc.get('metadata', {}).get('section', 'unknown')}"
                for doc in retrieved_guides
            )
        if history_contexts:
            sources.append("ordering_history")
        deduped_sources = list(dict.fromkeys(sources))

        raw_contexts = raw.get("retrieved_contexts")
        retrieved_contexts: list[str] = []
        if isinstance(raw_contexts, list):
            retrieved_contexts.extend(str(ctx) for ctx in raw_contexts if ctx)
        if not retrieved_contexts:
            retrieved_contexts.extend(history_contexts[:3])
            retrieved_contexts.extend([str(doc.get("content", "")) for doc in retrieved_guides[:2]])

        confidence_value = raw.get("confidence")
        confidence: float | None = None
        if confidence_value is not None:
            confidence = max(
                0.0,
                min(1.0, OrderingHistoryInsightService._to_float(confidence_value, default=0.0)),
            )

        if not kpis or not anomalies:
            raise ValueError("llm response missing required insight payload")

        return {
            "kpis": kpis,
            "anomalies": anomalies,
            "top_changed_items": changed_items,
            "sources": deduped_sources[:10],
            "retrieved_contexts": retrieved_contexts[:8],
            "confidence": confidence,
        }

    @staticmethod
    def _normalize_tone(value: Any) -> str:
        tone = str(value or "default").lower()
        if tone not in {"default", "primary", "warning", "danger", "success"}:
            return "default"
        return tone

    @staticmethod
    def _normalize_severity(value: Any) -> str:
        severity = str(value or "medium").lower()
        if severity not in {"low", "medium", "high"}:
            return "medium"
        return severity

    @staticmethod
    def _to_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
