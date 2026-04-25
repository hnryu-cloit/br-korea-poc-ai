from __future__ import annotations

import json
import logging

from common.gemini import Gemini
from schemas.contracts import (
    CampaignNarrativeRequest,
    CampaignNarrativeResponse,
    InsightSummarizeRequest,
    InsightSummarizeResponse,
    MenuInsightCard,
    MenuInsightsRequest,
    MenuInsightsResponse,
)

logger = logging.getLogger(__name__)


class InsightSummarizeService:
    """인사이트 섹션 요약 및 캠페인 서술을 구조화 JSON으로 생성"""

    def __init__(self, gemini_client: Gemini) -> None:
        self.gemini = gemini_client

    def summarize_insights(self, payload: InsightSummarizeRequest) -> InsightSummarizeResponse:
        """인사이트 4개 섹션 요약을 JSON으로 생성"""
        section_lines: list[str] = []
        for key in ("peak_hours", "channel_mix", "payment_mix", "menu_mix"):
            section = payload.sections.get(key)
            if not section:
                section_lines.append(f"- {key}: 데이터 없음")
                continue
            metrics_line = ", ".join(
                f"{m.get('label')}={m.get('value')}"
                for m in (section.metrics or [])
                if isinstance(m, dict)
            )
            section_lines.append(
                f"- {key}: title={section.title} | summary={section.summary} | metrics=[{metrics_line}]"
            )

        prompt = (
            "아래 매출 인사이트 섹션을 기반으로 각 섹션 summary를 1문장씩 작성하세요.\n"
            "입력 데이터 외 새로운 수치 생성 금지. 한국어, 실무 실행 관점.\n\n"
            f"점포: {payload.store_id}\n"
            f"기간: {payload.date_from or '미지정'} ~ {payload.date_to or '미지정'}\n"
            "섹션 데이터:\n"
            + "\n".join(section_lines)
            + '\n\n반드시 아래 JSON 형식으로만 응답하세요:\n'
            '{"peak_hours": "...", "channel_mix": "...", "payment_mix": "...", "menu_mix": "..."}'
        )

        res_str = self.gemini.call_gemini_text(prompt, response_type="application/json")
        data = json.loads(res_str)
        return InsightSummarizeResponse(
            peak_hours=str(data.get("peak_hours") or ""),
            channel_mix=str(data.get("channel_mix") or ""),
            payment_mix=str(data.get("payment_mix") or ""),
            menu_mix=str(data.get("menu_mix") or ""),
        )

    def generate_menu_insights(self, payload: MenuInsightsRequest) -> MenuInsightsResponse:
        """수익 구조·메뉴 믹스·매출 집중도 데이터로 점주 운영 액션 카드 3개 생성"""
        prompt = (
            "아래 매장 운영 데이터를 분석해서 점주가 바로 실행 가능한 운영 액션 카드 3개를 생성하세요.\n\n"
            "카드 구성 (반드시 이 3가지 순서, title은 정확히 일치):\n"
            "1. 수익 구조 인사이트 — profitability_data 기반 마진·할인 부담·순이익 진단\n"
            "2. 메뉴 믹스 인사이트 — product_mix_data 기반 상위 상품 비중·단가·다양성 분석\n"
            "3. 매출 집중도 (리스크) — concentration_data 기반 단일 상품 의존 리스크 진단\n\n"
            "규칙:\n"
            "- summary: 한국어 1~2문장, '점주가 무엇을 해야 하는지'가 한 줄에 떨어지게\n"
            "- metrics: 입력 데이터 수치만 사용 (최대 4개), label·value·detail 구성\n"
            "- actions: 점주가 즉시 실행 가능한 문장 2~3개 (생산/진열/할인/구성 관점)\n"
            "- 데이터가 없는 항목은 '데이터 없음'으로 표시, 임의 수치 생성 금지\n\n"
            f"점포: {payload.store_id}\n"
            f"기간: {payload.date_from or '미지정'} ~ {payload.date_to or '미지정'}\n\n"
            f"[수익 구조 데이터]\n{json.dumps(payload.profitability_data, ensure_ascii=False)}\n\n"
            f"[메뉴 믹스 데이터]\n{json.dumps(payload.product_mix_data, ensure_ascii=False)}\n\n"
            f"[매출 집중도 데이터]\n{json.dumps(payload.concentration_data, ensure_ascii=False)}\n\n"
            '반드시 아래 JSON 형식으로만 응답하세요:\n'
            '{"cards": ['
            '{"title": "수익 구조 인사이트", "summary": "...", "metrics": [{"label": "...", "value": "...", "detail": "..."}], "actions": ["...", "..."]},'
            '{"title": "메뉴 믹스 인사이트", "summary": "...", "metrics": [...], "actions": [...]},'
            '{"title": "매출 집중도 (리스크)", "summary": "...", "metrics": [...], "actions": [...]}'
            ']}'
        )

        res_str = self.gemini.call_gemini_text(prompt, response_type="application/json")
        data = json.loads(res_str)
        cards = [
            MenuInsightCard(
                title=str(card.get("title") or ""),
                summary=str(card.get("summary") or ""),
                metrics=card.get("metrics") or [],
                actions=card.get("actions") or [],
                status="active",
            )
            for card in (data.get("cards") or [])
            if isinstance(card, dict)
        ]
        if not cards:
            raise RuntimeError("Gemini가 인사이트 카드를 생성하지 못했습니다.")
        return MenuInsightsResponse(cards=cards)

    def generate_campaign_narrative(self, payload: CampaignNarrativeRequest) -> CampaignNarrativeResponse:
        """캠페인 효과 요약 1문장과 실행 액션 2개를 JSON으로 생성"""
        prompt = (
            "아래 캠페인 데이터를 바탕으로 summary 1문장과 action 2개를 생성하세요.\n"
            "입력 수치만 사용, 임의 생성 금지. action은 실행형 문장.\n\n"
            f"점포: {payload.store_id}\n"
            f"campaign_code: {payload.campaign_code}\n"
            f"campaign_name: {payload.campaign_name}\n"
            f"discount_cost: {payload.discount_cost}\n"
            f"uplift_revenue: {payload.uplift_revenue}\n"
            f"roi_pct: {payload.roi_pct}\n"
            f"periods: {payload.periods}\n\n"
            '반드시 아래 JSON 형식으로만 응답하세요:\n'
            '{"summary": "...", "action1": "...", "action2": "..."}'
        )

        res_str = self.gemini.call_gemini_text(prompt, response_type="application/json")
        data = json.loads(res_str)
        return CampaignNarrativeResponse(
            summary=str(data.get("summary") or ""),
            action1=str(data.get("action1") or ""),
            action2=str(data.get("action2") or ""),
        )
