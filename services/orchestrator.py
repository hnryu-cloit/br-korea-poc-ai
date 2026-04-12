from __future__ import annotations

from typing import Any, Dict, Optional

from common.logger import init_logger
from common.gemini import Gemini
from services.sales_analyzer import SalesAnalyzer
from services.production_service import ProductionService
from services.ordering_service import OrderingService
from services.rag_service import RAGService
from services.query_classifier import QueryClassifier
from services.semantic_layer import SemanticLayer
from evaluators.basic import QualityEvaluator

logger = init_logger("orchestrator")

class AgentOrchestrator:
    """
    에이전트 오케스트레이터 (최종 고도화 버전):
    1. 지능형 시맨틱 레이어를 통한 비즈니스 로직 매핑
    2. 다중 에이전트 및 RAG 연동
    3. 실시간 AI 응답 품질 평가 및 신뢰도 관리
    """
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.classifier = QueryClassifier()
        self.semantic_layer = SemanticLayer()
        self.evaluator = QualityEvaluator(gemini_client)
        
        self.rag_service = RAGService(gemini_client)
        self.sales_agent = SalesAnalyzer(gemini_client)
        self.prod_agent = ProductionService(gemini_client)
        self.order_agent = OrderingService(gemini_client)

    async def handle_request(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> Any:
        """
        사용자 질문을 통합 처리하여 검증된 응답을 반환합니다.
        """
        logger.info(f"오케스트레이션 요청 시작: '{prompt[:50]}'")
        
        # 1. 시맨틱 비즈니스 로직 매핑 (Semantic Layer)
        business_logic = self.semantic_layer.get_logic(prompt)
        logger.info(f"적용될 비즈니스 로직: {business_logic}")
        
        # 2. 의도 분류 및 가드레일 (Predictor Layer)
        intent = self.classifier.classify(prompt)
        if intent == "SENSITIVE":
             return self.sales_agent.analyze(prompt)

        # 3. RAG(지식 검색) 시도 및 품질 평가
        rag_result = self.rag_service.generate_with_rag(prompt)
        
        if rag_result.get("text") and "가이드를 찾을 수 없습니다" not in rag_result["text"]:
            # 품질 평가 (실시간 신뢰도 측정)
            confidence_score = self.evaluator.evaluate_response(
                query=prompt, 
                response=rag_result["text"], 
                context=rag_result.get("sources", [])
            )
            
            if confidence_score >= 0.7:
                logger.info("신뢰할 수 있는 RAG 응답으로 판명됨.")
                return {
                    "text": rag_result["text"],
                    "evidence": [f"출처: {s}" for s in rag_result["sources"]] + [f"신뢰도 지수: {confidence_score:.2f}"],
                    "actions": ["매뉴얼 상세 보기", "관리자 추가 문의"]
                }
            else:
                logger.warning(f"신뢰도 저하 ({confidence_score:.2f}). 데이터 분석 에이전트로 전환.")

        # 4. 도메인 에이전트 분석 수행
        # (생략: 생산/주문 에이전트 호출 로직 유지)
        
        # 5. 최종 매출 분석 및 가드레일 적용
        analysis_result = self.sales_agent.analyze(payload=SalesQueryRequest(store_id="default_store", query=prompt, raw_data_context=None))
        
        # SalesQueryResponse 형태를 유지하여 프론트엔드 연동에 문제없게 함
        return analysis_result
