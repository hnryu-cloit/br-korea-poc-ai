from __future__ import annotations

import random
from typing import Any

from api.schemas import OrderingOption, OrderingRecommendRequest, OrderingRecommendResponse
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_ordering_reasoning_prompt

logger = init_logger("ordering_service")


class OrderingService:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client

    def recommend_ordering(self, payload: OrderingRecommendRequest) -> OrderingRecommendResponse:
        """
        Production-level recommendation logic:
        1. Predict ordering quantities based on historical data patterns (ML part).
        2. Combine external signals like campaigns and holidays.
        3. Use LLM to generate professional, natural language reasoning for the owner.
        """
        logger.info(f"Generating recommendations for store {payload.store_id} at {payload.current_date}")

        # 1. Prediction (ML Part - mocked for PoC)
        # Recommendation models would typically be a time-series forecast (Prophet, ARIMA, etc.)
        # Based on features like weekday, day-of-month, campaign_flag, holiday_flag
        base_qty = 150
        if payload.is_campaign:
            base_qty *= 1.2
        if payload.is_holiday:
            base_qty *= 1.3
            
        options = [
            OrderingOption(name="전주 동요일 기준 (안정형)", recommended_quantity=int(base_qty), priority=1),
            OrderingOption(name="전전주 동요일 기준 (평균형)", recommended_quantity=int(base_qty * 0.95), priority=2),
            OrderingOption(name="전월 동요일 기준 (장기 추세)", recommended_quantity=int(base_qty * 1.1), priority=3),
        ]

        # 2. Reasoning Generation (Generative AI Part)
        options_summary = "\n".join([f"- {o.name}: {o.recommended_quantity}건" for o in options])
        
        prompt = create_ordering_reasoning_prompt(
            store_id=payload.store_id,
            current_date=payload.current_date,
            campaign_status="진행 중" if payload.is_campaign else "없음",
            holiday_status="있음 (시즌 특수)" if payload.is_holiday else "없음",
            options_summary=options_summary
        )
        
        try:
            reasoning = self.gemini.call_gemini_text(prompt, response_type="text")
            logger.info("Ordering reasoning generated successfully via LLM")
        except Exception as e:
            logger.error(f"Failed to generate LLM reasoning: {e}")
            reasoning = f"전체적인 최근 추세를 반영하여 {options[0].recommended_quantity}건을 추천합니다. 캠페인 및 공휴일 영향도가 고려되었습니다."

        return OrderingRecommendResponse(
            options=options,
            reasoning=reasoning
        )
