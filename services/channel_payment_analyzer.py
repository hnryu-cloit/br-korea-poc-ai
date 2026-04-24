from __future__ import annotations

from common.gemini import Gemini
from common.logger import init_logger
from schemas.contracts import SalesQueryRequest
from services.grounded_workflow import GroundedWorkflow

logger = init_logger(__name__)


class ChannelPaymentAnalyzer:
    """채널·결제수단 질의를 공통 grounded 워크플로우로 라우팅"""

    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client

    def analyze(self, payload: SalesQueryRequest) -> dict:
        logger.info("채널/결제 분석 요청: store=%s, query=%s", payload.store_id, payload.query[:50])
        workflow = GroundedWorkflow(self.gemini)
        result = workflow.run(
            query=payload.query,
            store_id=payload.store_id,
            domain="channel",
            reference_date=payload.business_date,
        )
        return {
            "answer": {
                "text": result.get("text", ""),
                "evidence": result.get("evidence", []),
                "actions": result.get("actions", []),
            },
            "source_data_period": "실시간 DB 연동 (Grounded Analysis)",
            "request_context": {
                "store_id": payload.store_id,
                "business_date": payload.business_date,
                "business_time": getattr(payload, "business_time", None),
                "prompt": payload.query,
                "domain": "channel",
            },
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
