from __future__ import annotations

import json
import logging
from typing import Any

from common.gemini import Gemini

logger = logging.getLogger(__name__)


class MarketInsightService:
    """상권 데이터 기반 실행 인사이트 생성"""

    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client

    def generate(
        self,
        *,
        audience: str,
        scope: dict[str, Any],
        market_data: dict[str, Any],
        branch_snapshots: list[dict[str, Any]],
        store_name: str | None,
    ) -> dict[str, Any]:
        prompt = self._build_prompt(
            audience=audience,
            scope=scope,
            market_data=market_data,
            branch_snapshots=branch_snapshots,
            store_name=store_name,
        )
        try:
            raw = self.gemini.call_gemini_text(prompt, response_type="application/json")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("LLM 응답 JSON 타입이 dict가 아님")
            return self._normalize_response(parsed, audience=audience)
        except (json.JSONDecodeError, ValueError, TypeError, RuntimeError) as exc:
            logger.exception("상권 인사이트 생성 실패")
            raise RuntimeError("상권 인사이트 생성 실패") from exc

    @staticmethod
    def _build_prompt(
        *,
        audience: str,
        scope: dict[str, Any],
        market_data: dict[str, Any],
        branch_snapshots: list[dict[str, Any]],
        store_name: str | None,
    ) -> str:
        payload = {
            "audience": audience,
            "store_name": store_name,
            "scope": scope,
            "market_data": market_data,
            "branch_snapshots": branch_snapshots,
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        return f"""
당신은 던킨 상권 분석 리포트를 작성하는 분석가입니다.
아래 입력 데이터만 근거로 JSON을 생성하세요.

규칙:
1) 입력 데이터에 없는 사실(브랜드명, 뉴스, 리포트, 임대료 수치)을 절대 생성하지 마세요.
2) 수치가 없으면 "미확인" 또는 "데이터 미제공"으로 명시하세요.
3) audience가 store_owner면 단일 매장 실행 액션 중심으로 작성하세요.
4) audience가 hq_admin이면 전체 지점 비교/우선순위 중심으로 작성하세요.
5) 반드시 아래 JSON 스키마를 따르세요.

JSON 스키마:
{{
  "executive_summary": "string",
  "key_insights": [{{"title": "string", "description": "string", "impact": "high|medium|low"}}],
  "risk_warnings": [{{"title": "string", "description": "string", "mitigation": "string"}}],
  "action_plan": [{{"priority": 1, "title": "string", "action": "string", "expected_effect": "string"}}],
  "branch_scoreboard": [{{"store_id": "string", "store_name": "string", "growth_rate": "string", "risk_level": "high|medium|low", "summary": "string"}}],
  "report_markdown": "string",
  "evidence_refs": ["string"]
}}

입력 데이터(JSON):
{serialized}
""".strip()

    @staticmethod
    def _normalize_response(raw: dict[str, Any], *, audience: str) -> dict[str, Any]:
        key_insights = raw.get("key_insights")
        risk_warnings = raw.get("risk_warnings")
        action_plan = raw.get("action_plan")
        branch_scoreboard = raw.get("branch_scoreboard")
        evidence_refs = raw.get("evidence_refs")
        report_markdown = raw.get("report_markdown")

        return {
            "executive_summary": str(raw.get("executive_summary") or ""),
            "key_insights": key_insights if isinstance(key_insights, list) else [],
            "risk_warnings": risk_warnings if isinstance(risk_warnings, list) else [],
            "action_plan": action_plan if isinstance(action_plan, list) else [],
            "branch_scoreboard": branch_scoreboard if isinstance(branch_scoreboard, list) else [],
            "report_markdown": str(report_markdown or ""),
            "evidence_refs": evidence_refs if isinstance(evidence_refs, list) else [],
            "audience": audience,
            "source": "ai",
        }
