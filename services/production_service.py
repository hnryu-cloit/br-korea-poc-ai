from __future__ import annotations

import datetime
from typing import Any

from api.schemas import ProductionPredictRequest, ProductionPredictResponse
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_production_alarm_prompt
from services.predictor import InventoryPredictor

logger = init_logger("production_service")


class ProductionService:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.predictor = InventoryPredictor()

    def predict_stock(self, payload: ProductionPredictRequest) -> ProductionPredictResponse:
        """
        Production-level implementation:
        1. Calls ML Predictor for next-hour stock.
        2. Determines risk levels.
        3. Uses LLM to generate professional, natural language alarm messages.
        """
        logger.info(f"Predicting stock for SKU: {payload.sku}")

        # 1. Prediction (ML Part)
        predicted_stock, confidence = self.predictor.predict_next_stock(
            history=payload.history, 
            current_stock=payload.current_stock
        )

        # 2. Risk Detection logic
        risk_detected = predicted_stock < 10.0
        risk_status = "위험 (품절 예상)" if risk_detected else "안정"
        
        stockout_at = "N/A"
        if risk_detected:
            # Estimate stockout time (mocked for PoC)
            now = datetime.datetime.now()
            # Simple heuristic: if stock is 10 and predicting 0, it means it will be 0 in 1 hour
            # Calculate estimated stockout based on predicted slope
            stockout_at = (now + datetime.timedelta(minutes=45)).strftime("%H:%M")

        # 3. Message Generation (Generative AI Part)
        prompt = create_production_alarm_prompt(
            sku=payload.sku,
            current_stock=payload.current_stock,
            predicted_stock=predicted_stock,
            risk_status=risk_status,
            stockout_at=stockout_at,
            pattern_4w=payload.pattern_4w
        )
        
        try:
            alert_message = self.gemini.call_gemini_text(prompt, response_type="text")
            logger.info("Alert message generated successfully via LLM")
        except Exception as e:
            logger.error(f"Failed to generate LLM alert: {e}")
            alert_message = f"현재 {payload.sku} 재고가 {payload.current_stock}개입니다. 1시간 뒤 {predicted_stock:.1f}개로 예상되어 생산이 필요할 수 있습니다."

        return ProductionPredictResponse(
            sku=payload.sku,
            predicted_stock_1h=predicted_stock,
            risk_detected=risk_detected,
            stockout_expected_at=stockout_at if risk_detected else None,
            alert_message=alert_message,
            confidence=confidence
        )
