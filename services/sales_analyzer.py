from __future__ import annotations

import json
from api.schemas import SalesQueryResponse
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_sales_analysis_prompt
from services.predictor import QueryClassifier

logger = init_logger("sales_analyzer")


class SalesAnalyzer:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.classifier = QueryClassifier()

    def analyze(self, prompt: str) -> SalesQueryResponse:
        """
        Production-level Sales Analysis Agent:
        1. Classifies query type (ML/DL part).
        2. Applies security guardrails for sensitive data.
        3. Generates high-quality, data-driven responses using Gemini.
        """
        logger.info(f"Analyzing sales query: {prompt[:50]}")

        # 1. Classification & Guardrail (ML Part)
        query_type = self.classifier.classify(prompt)
        
        if query_type == "SENSITIVE":
            logger.warning("Sensitive query detected. Blocking response.")
            return SalesQueryResponse(
                text="보안 정책에 따라 민감한 경영 정보(수익, 원가, 상세 이익 등)를 포함한 질문은 직접 분석이 제한됩니다. 권한이 있는 대시보드에서 직접 확인해 주세요.",
                evidence=["민감 정보 식별 알고리즘 적용"],
                actions=["보안 대시보드 바로가기", "본사 관리팀 문의"]
            )

        # 2. Generation (Generative AI Part)
        gemini_prompt = create_sales_analysis_prompt(prompt)
        
        try:
            response_json = self.gemini.call_gemini_text(gemini_prompt, response_type="application/json")
            data = json.loads(response_json)
            logger.info("Sales analysis response generated successfully via LLM")
            return SalesQueryResponse(**data)
        except Exception as e:
            logger.error(f"Error during sales analysis or parsing: {e}")
            return SalesQueryResponse(
                text="요청하신 질의를 분석하는 중에 문제가 발생했습니다. 조금 더 구체적인 질문을 입력해 주시겠어요?",
                evidence=["데이터 분석 엔진 오류"],
                actions=["질의 재입력", "시스템 관리자에게 문의"]
            )
