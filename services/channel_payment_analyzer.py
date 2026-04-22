from __future__ import annotations

from common.gemini import Gemini
from common.logger import init_logger
from schemas.contracts import SalesQueryRequest
from services.grounded_workflow import GroundedWorkflow

logger = init_logger(__name__)


class ChannelPaymentAnalyzer:
    """Channel and payment questions routed through the shared grounded workflow."""

    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client

    def analyze(self, payload: SalesQueryRequest) -> dict:
        logger.info("채널/결제 분석 요청: store=%s, query=%s", payload.store_id, payload.query[:50])
        workflow = GroundedWorkflow(self.gemini)
        result = workflow.run(query=payload.query, store_id=payload.store_id, domain="channel")
        return {
            "answer": {
                "text": result.get("text", ""),
                "evidence": result.get("evidence", []),
                "actions": result.get("actions", []),
            },
            "source_data_period": "실시간 DB 연동 (Grounded Analysis)",
            "queried_period": result.get("queried_period"),
            "grounding": {
                "keywords": result.get("keywords", []),
                "intent": result.get("intent"),
                "relevant_tables": result.get("relevant_tables", []),
                "sql": result.get("sql"),
                "row_count": result.get("row_count", 0),
            },
            "data_lineage": result.get("data_lineage", []),
        }
