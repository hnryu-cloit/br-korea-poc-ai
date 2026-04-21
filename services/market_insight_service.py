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
            return self._fallback_response(
                audience=audience,
                scope=scope,
                store_name=store_name,
                error_message=str(exc),
            )

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

    @staticmethod
    def _fallback_response(
        *,
        audience: str,
        scope: dict[str, Any],
        store_name: str | None,
        error_message: str,
    ) -> dict[str, Any]:
        label = store_name or "대상 매장"
        scope_text = ", ".join(f"{k}={v}" for k, v in scope.items() if v not in (None, "", "전체"))
        summary = (
            f"{label} 상권 인사이트를 생성하지 못해 기본 분석으로 대체했습니다."
            if audience == "store_owner"
            else "전체 지점 상권 인사이트를 생성하지 못해 기본 분석으로 대체했습니다."
        )
        return {
            "executive_summary": summary,
            "key_insights": [
                {
                    "title": "AI 인사이트 생성 실패",
                    "description": "요청 시점에 생성 오류가 발생했습니다. 기본 지표 기반 해석만 제공합니다.",
                    "impact": "medium",
                }
            ],
            "risk_warnings": [
                {
                    "title": "분석 해상도 저하",
                    "description": "서술형 분석이 fallback 처리되어 상세 문맥이 제한됩니다.",
                    "mitigation": "잠시 후 재시도하거나 필터 범위를 축소해 주세요.",
                }
            ],
            "action_plan": [
                {
                    "priority": 1,
                    "title": "지표 재확인",
                    "action": "핵심 차트의 최근 4주 추세와 피크 시간대를 먼저 확인합니다.",
                    "expected_effect": "운영 의사결정 지연을 최소화할 수 있습니다.",
                }
            ],
            "branch_scoreboard": [],
            "report_markdown": (
                f"# 상권 분석 리포트 (fallback)\n\n"
                f"- audience: {audience}\n"
                f"- 대상: {label}\n"
                f"- scope: {scope_text or '기본'}\n"
                f"- 상태: AI 생성 실패로 기본 분석 전환\n"
                f"- 오류: {error_message}\n"
            ),
            "evidence_refs": ["fallback"],
            "audience": audience,
            "source": "fallback",
        }
