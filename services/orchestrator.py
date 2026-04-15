from __future__ import annotations

from typing import Any, Dict, Optional

from common.logger import init_logger
from common.gemini import Gemini
from services.sales_analyzer import SalesAnalyzer
from services.channel_payment_analyzer import ChannelPaymentAnalyzer
from services.production_service import ProductionService
from services.ordering_service import OrderingService
from services.rag_service import RAGService
from services.query_classifier import QueryClassifier
from services.semantic_layer import SemanticLayer
from schemas.contracts import SalesQueryRequest
from common.evaluator import QualityEvaluator

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
        self.channel_agent = ChannelPaymentAnalyzer(gemini_client)
        self.prod_agent = ProductionService(gemini_client)
        self.order_agent = OrderingService(gemini_client)

    async def handle_request(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> Any:
        """
        사용자 질문을 통합 처리하여 검증된 응답을 반환합니다.
        """
        logger.info(f"오케스트레이션 요청 시작: '{prompt[:50]}'")
        classifier_result = self.classifier.classify_details(prompt)
        intent = classifier_result["query_type"]
        safe_prompt = classifier_result["masked_query"]
        masked_fields = classifier_result["masked_fields"]
        
        # 1. 시맨틱 비즈니스 로직 매핑 (Semantic Layer)
        business_logic = self.semantic_layer.get_logic(safe_prompt)
        logger.info(f"적용될 비즈니스 로직: {business_logic}")
        
        # 2. 의도 분류 및 가드레일 (Predictor Layer)
        if intent == "SENSITIVE":
            return {
                "text": "민감정보가 포함된 질의로 분류되어 직접 응답을 제한합니다.",
                "evidence": ["민감 키워드 가드레일 적용", f"masked_fields={masked_fields}"],
                "actions": ["민감정보를 제외하고 다시 질문", "표준 지표 기준으로 재질의"],
                "query_type": intent,
                "processing_route": "policy_block",
                "blocked": True,
                "masked_fields": masked_fields,
            }

        # 3. RAG(지식 검색) 시도 및 품질 평가
        rag_result = self.rag_service.generate_with_rag(safe_prompt)
        
        if rag_result.get("text") and "가이드를 찾을 수 없습니다" not in str(rag_result["text"]):
            # 품질 평가 (실시간 신뢰도 측정)
            confidence_score = self.evaluator.evaluate_response(
                query=safe_prompt, 
                response=str(rag_result["text"]), 
                context=rag_result.get("sources", [])
            )
            
            if confidence_score >= 0.7:
                logger.info("신뢰할 수 있는 RAG 응답으로 판명됨.")
                return {
                    "text": rag_result["text"],
                    "evidence": [f"출처: {s}" for s in rag_result["sources"]] + [f"신뢰도 지수: {confidence_score:.2f}"],
                    "actions": ["매뉴얼 상세 보기", "관리자 추가 문의"],
                    "query_type": intent,
                    "processing_route": "rag",
                    "blocked": False,
                    "masked_fields": masked_fields,
                }
            else:
                logger.warning(f"신뢰도 저하 ({confidence_score:.2f}). 데이터 분석 에이전트로 전환.")

        # 4. 도메인 에이전트 분석 수행
        # 채널 및 결제수단 특화 의도인 경우 전용 분석 에이전트 호출
        if intent == "CHANNEL":
            logger.info("Channel/Payment 전용 분석 에이전트 호출")
            result = self.channel_agent.analyze(payload=SalesQueryRequest(store_id="default_store", query=safe_prompt))
            if hasattr(result, "model_dump"):
                dumped = result.model_dump()
                dumped["query_type"] = intent
                dumped["processing_route"] = "channel_agent"
                dumped["blocked"] = False
                dumped["masked_fields"] = masked_fields
                return dumped
            return result

        # 생산 관리 에이전트 호출
        _PRODUCTION_KW = ["생산", "재고", "품절", "제조", "만들", "소진", "생산량", "보유량"]
        if any(kw in safe_prompt for kw in _PRODUCTION_KW):
            logger.info("생산 관리 에이전트 호출")
            prod_prompt = (
                f"다음 생산 관련 질의에 답변하세요: {safe_prompt}\n\n"
                "생산 관리 관점에서 재고 예측, 생산 타이밍, 위험 감지 정보를 포함해 한국어로 답변하세요."
            )
            try:
                text = self.gemini.call_gemini_text(prod_prompt)
            except Exception as exc:
                logger.warning("생산 에이전트 Gemini 호출 실패: %s", exc)
                text = "생산 관리 화면에서 SKU별 재고 현황과 1시간 후 예측값을 확인해주세요."
            return {
                "text": text,
                "evidence": ["생산 관리 에이전트 분석"],
                "actions": ["생산 현황 확인", "재고 수준 점검", "생산 추천 확인"],
                "query_type": "GENERAL",
                "processing_route": "production_agent",
                "blocked": False,
                "masked_fields": masked_fields,
            }

        # 주문 관리 에이전트 호출
        _ORDERING_KW = ["주문", "발주", "마감", "배송", "납품", "주문량", "주문 수"]
        if any(kw in safe_prompt for kw in _ORDERING_KW):
            logger.info("주문 관리 에이전트 호출")
            order_prompt = (
                f"다음 주문 관련 질의에 답변하세요: {safe_prompt}\n\n"
                "주문 관리 관점에서 추천 수량, 마감 시간, 시즌성 정보를 포함해 한국어로 답변하세요."
            )
            try:
                text = self.gemini.call_gemini_text(order_prompt)
            except Exception as exc:
                logger.warning("주문 에이전트 Gemini 호출 실패: %s", exc)
                text = "주문 관리 화면에서 3가지 추천 옵션을 확인하고 마감 시간 전에 주문해주세요."
            return {
                "text": text,
                "evidence": ["주문 관리 에이전트 분석"],
                "actions": ["주문 옵션 확인", "마감 시간 확인", "주문 수량 결정"],
                "query_type": "GENERAL",
                "processing_route": "ordering_agent",
                "blocked": False,
                "masked_fields": masked_fields,
            }

        # 5. 최종 매출 분석 및 가드레일 적용
        analysis_result = self.sales_agent.analyze(payload=SalesQueryRequest(store_id="default_store", query=safe_prompt))
        
        # SalesQueryResponse 형태를 유지하여 프론트엔드 연동에 문제없게 함
        if hasattr(analysis_result, "model_dump"):
            dumped = analysis_result.model_dump()
            dumped["query_type"] = intent
            dumped["processing_route"] = "sales_agent"
            dumped["blocked"] = False
            dumped["masked_fields"] = masked_fields
            return dumped
        return analysis_result
