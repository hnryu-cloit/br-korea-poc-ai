from __future__ import annotations

import json
import logging

from common.gemini import Gemini
from common.logger import init_logger
from schemas.contracts import SalesInsight, SalesQueryRequest, SalesQueryResponse
from services.sales_analysis_engine import SalesAnalysisEngine

logger = init_logger("channel_payment_analyzer")


class ChannelPaymentAnalyzer:
    """
    채널(온라인/오프라인/배달)별 및 결제수단별 매출 트렌드 분석 에이전트.
    SalesAnalysisEngine의 채널 분석 로직을 위임받아 Gemini 인사이트를 생성한다.
    """

    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.analysis_engine = SalesAnalysisEngine()

    def analyze(self, payload: SalesQueryRequest) -> SalesQueryResponse:
        logger.info("채널/결제 분석 요청: store=%s, query=%s", payload.store_id, payload.query[:50])

        channel_res = self.analysis_engine.analyze_real_channel_mix(store_id=payload.store_id)
        profit_res = self.analysis_engine.simulate_real_profitability(store_id=payload.store_id)

        prompt = f"""
당신은 편의점·베이커리 프랜차이즈 매장의 채널 및 결제수단 분석 전문가입니다.

사용자 질문: {payload.query}

[채널 분석 결과]
- 배달 비중: {channel_res.get('delivery_rate', 0)}%
- 트렌드: {channel_res.get('trend', '데이터 없음')}
- 온라인 매출액: {channel_res.get('online_amt', 0):,.0f}원
- 오프라인 매출액: {channel_res.get('offline_amt', 0):,.0f}원

[수익성 분석]
- 추정 마진율: {profit_res.get('estimated_margin_rate', 0.3) * 100:.1f}%
- 추정 영업이익: {profit_res.get('estimated_profit', 0):,.0f}원

**지시사항:**
1. 채널별·결제수단별 트렌드와 개선 포인트를 분석하세요.
2. 점주가 즉시 실행 가능한 액션 3가지를 제시하세요.
3. 프랜차이즈 가맹점 규정 내에서 제안하세요 (임의 할인·가격변경 금지).

응답은 반드시 아래 JSON 형식으로 주세요.
{{
    "text": "핵심 분석 요약",
    "evidence": ["근거 수치 1", "근거 수치 2"],
    "actions": ["액션 1", "액션 2", "액션 3"]
}}
"""
        try:
            response_json = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(response_json)
            insight = SalesInsight(
                text=data.get("text", ""),
                evidence=data.get("evidence", []),
                actions=data.get("actions", []),
            )
            return SalesQueryResponse(
                answer=insight,
                source_data_period="최근 4주",
                channel_analysis=channel_res,
                profit_simulation=profit_res,
            )
        except Exception as exc:
            logger.error("채널/결제 분석 오류: %s", exc)
            insight = SalesInsight(
                text="채널 및 결제수단 데이터를 분석했습니다.",
                evidence=[
                    f"배달 비중: {channel_res.get('delivery_rate', 0)}%",
                    f"트렌드: {channel_res.get('trend', '-')}",
                ],
                actions=["배달앱 운영 현황 점검", "결제수단별 비중 모니터링", "피크 시간대 채널 집중 관리"],
            )
            return SalesQueryResponse(
                answer=insight,
                source_data_period="최근 4주",
                channel_analysis=channel_res,
                profit_simulation=profit_res,
            )