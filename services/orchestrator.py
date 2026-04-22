from __future__ import annotations

from typing import Any

from common.evaluator import QualityEvaluator
from common.gemini import Gemini
from common.logger import init_logger
from schemas.contracts import SalesQueryRequest
from services.channel_payment_analyzer import ChannelPaymentAnalyzer
from services.ordering_service import OrderingService
from services.production_service import ProductionService
from services.query_classifier import QueryClassifier
from services.rag_service import RAGService
from services.sales_analyzer import SalesAnalyzer
from services.semantic_layer import SemanticLayer

logger = init_logger(__name__)


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

    async def handle_request(self, prompt: str, context: dict[str, Any] | None = None) -> Any:
        """
        사용자 질문을 통합 처리하여 검증된 응답을 반환합니다.
        """
        logger.info(f"오케스트레이션 요청 시작: '{prompt[:50]}'")
        store_id = "default_store"
        if context and isinstance(context.get("store_id"), str):
            candidate = context["store_id"].strip()
            if candidate:
                store_id = candidate

        classifier_result = self.classifier.classify_details(prompt)
        intent = classifier_result["query_type"]
        safe_prompt = classifier_result["masked_query"]
        masked_fields = classifier_result["masked_fields"]

        # POS 기기 등에서 넘어온 컨텍스트에서 매장 ID 추출 (기본값 POC_001)
        context = context or {}
        store_id = context.get("store_id", "POC_001")

        # 1. 시맨틱 비즈니스 로직 매핑 (Semantic Layer)
        _, business_logic = self.semantic_layer.parse_query_intent(safe_prompt)
        logger.info(f"적용될 비즈니스 로직: {business_logic}, 요청 매장: {store_id}")

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

        # 3. 도메인 에이전트 우선 분석 (특화 키워드 감지 시)
        # 💡 [중요] 분류기(Intent) 결과보다 명시적인 키워드를 우선하여 라우팅 정확도 향상
        
        # 생산 관리 에이전트 키워드 (부정형 및 구체적 수치 질문 포함)
        _PRODUCTION_KW = ["생산", "재고", "품절", "제조", "만들", "소진", "생산량", "보유량", "흐름", "안 한", "하지 않은", "미생산"]
        # 원본 prompt와 safe_prompt 모두에서 키워드 체크
        check_text = (prompt + safe_prompt).replace(" ", "")
        
        if any(kw.replace(" ", "") in check_text for kw in _PRODUCTION_KW):
            logger.info("생산 관리 에이전트 호출 (Prioritized by Keyword)")
            try:
                # payload에 주입되는 질문은 시스템 지침을 제외한 순수 질문(safe_prompt) 사용
                result = self.prod_agent.analyze(
                    payload=SalesQueryRequest(store_id=store_id, query=safe_prompt)
                )
                if isinstance(result, dict):
                    result["query_type"] = "PRODUCTION"
                    result["processing_route"] = "production_agent_grounded"
                    result["blocked"] = False
                    result["masked_fields"] = masked_fields
                    return result
            except Exception as exc:
                logger.error("생산 에이전트 분석 실패: %s", exc)

        # 주문 관리 에이전트 키워드
        _ORDERING_KW = ["주문", "발주", "마감", "배송", "납품", "주문량", "주문 수", "주문안"]
        if any(kw.replace(" ", "") in check_text for kw in _ORDERING_KW):
            logger.info("주문 관리 에이전트 호출 (Prioritized by Keyword)")
            try:
                result = self.order_agent.analyze(
                    payload=SalesQueryRequest(store_id=store_id, query=safe_prompt)
                )
                if isinstance(result, dict):
                    result["query_type"] = "ORDERING"
                    result["processing_route"] = "ordering_agent_grounded"
                    result["blocked"] = False
                    result["masked_fields"] = masked_fields
                    return result
            except Exception as exc:
                logger.error("주문 에이전트 분석 실패: %s", exc)

        # 채널 및 결제수단 특화 의도 (키워드 매칭이 안 된 경우에만 수행)
        if intent == "CHANNEL":
            logger.info("Channel/Payment 전용 분석 에이전트 호출")
            result = self.channel_agent.analyze(
                payload=SalesQueryRequest(store_id=store_id, query=safe_prompt)
            )
            if hasattr(result, "model_dump"):
                dumped = result.model_dump()
                dumped["query_type"] = intent
                dumped["processing_route"] = "channel_agent"
                dumped["blocked"] = False
                dumped["masked_fields"] = masked_fields
                return dumped
            if isinstance(result, dict):
                result["query_type"] = intent
                result["processing_route"] = "channel_agent"
                result["blocked"] = False
                result["masked_fields"] = masked_fields
            return result

        # 4. RAG(지식 검색) 시도 및 품질 평가 (범용 질의 및 매뉴얼 질의)
        rag_result = self.rag_service.generate_with_rag(safe_prompt)

        if rag_result.get("text") and "가이드를 찾을 수 없습니다" not in str(rag_result["text"]):
            # 품질 평가 (실시간 신뢰도 측정)
            confidence_score = self.evaluator.evaluate_response(
                query=safe_prompt,
                response=str(rag_result["text"]),
                context=rag_result.get("sources", []),
            )

            if confidence_score >= 0.7:
                logger.info("신뢰할 수 있는 RAG 응답으로 판명됨.")
                return {
                    "text": rag_result["text"],
                    "evidence": [f"출처: {s}" for s in rag_result["sources"]]
                    + [f"신뢰도 지수: {confidence_score:.2f}"],
                    "actions": ["매뉴얼 상세 보기", "관리자 추가 문의"],
                    "query_type": intent,
                    "processing_route": "rag",
                    "blocked": False,
                    "masked_fields": masked_fields,
                }
            else:
                logger.warning(
                    f"신뢰도 저하 ({confidence_score:.2f}). 데이터 분석 에이전트로 전환."
                )

        # 5. 최종 매출 분석 및 가드레일 적용
        analysis_result = self.sales_agent.analyze(
            payload=SalesQueryRequest(store_id=store_id, query=safe_prompt)
        )

        # SalesQueryResponse 형태를 유지하여 프론트엔드 연동에 문제없게 함
        if hasattr(analysis_result, "model_dump"):
            dumped = analysis_result.model_dump()
            dumped["query_type"] = intent
            dumped["processing_route"] = "sales_agent"
            dumped["blocked"] = False
            dumped["masked_fields"] = masked_fields
            return dumped
        if isinstance(analysis_result, dict):
            analysis_result["query_type"] = intent
            analysis_result["processing_route"] = "sales_agent"
            analysis_result["blocked"] = False
            analysis_result["masked_fields"] = masked_fields
        return analysis_result
