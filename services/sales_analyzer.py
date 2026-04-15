from __future__ import annotations
import json
from schemas.contracts import (
    SalesQueryRequest,
    SalesQueryResponse,
    SalesInsight,
    ProfitabilitySimulationResponse,
)
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_sales_analysis_prompt
from services.query_classifier import QueryClassifier
from services.semantic_layer import SemanticLayer
from services.rag_service import RAGService
from services.sales_agent import SalesAnalysisAgent

logger = init_logger("sales_analyzer")

class SalesAnalyzer:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.classifier = QueryClassifier()
        self.semantic_layer = SemanticLayer()
        self.rag_service = RAGService(gemini_client)
        self.agent = SalesAnalysisAgent()

    def analyze(self, payload: SalesQueryRequest) -> SalesQueryResponse:
        """
        매출 분석 서비스 메인 진입점:
        1. 시맨틱 캐시 확인
        2. 민감 질의 가드레일 적용
        3. SQL vs RAG 라우팅 결정
        4. 에이전트 기반 핵심 분석 및 Gemini 응답 생성
        """
        logger.info(f"매출 분석 질의 처리: {payload.query[:50]}")

        # 시맨틱 캐시 조회 — 동일 의도의 이전 분석 결과 재활용
        cached_result = self.rag_service.lookup_qa_cache(store_id=payload.store_id, query=payload.query)
        if cached_result:
            intent_keywords = ["조합", "같이", "함께", "세트", "교차", "배달", "채널", "비교", "성장", "수익", "이익"]
            current_query_keywords = [k for k in intent_keywords if k in payload.query]
            cached_query = cached_result.get("_original_query", "")
            
            is_intent_match = any(k in cached_query for k in current_query_keywords) if current_query_keywords else True
            
            if is_intent_match:
                logger.info(f"매장 {payload.store_id}의 시맨틱 캐시 적중! 맞춤형 답변을 재생성합니다.")
            
            cached_channel = cached_result.get("channel_analysis", {})
            cached_profit = cached_result.get("profit_simulation", {})
            cached_evidence = cached_result.get("answer", {}).get("evidence", [])

            # 캐시 수치를 현재 질문에 맞게 재조합한 프롬프트 구성
            cache_prompt = f"""
            사용자 질문: {payload.query}
            
            [미리 분석된 매장 수치 데이터 (캐시)]
            - 채널: {cached_channel}
            - 수익성: {cached_profit}
            - 기존 근거: {cached_evidence}
            
            **지시사항:**
            1. 위 제공된 수치 데이터를 활용하되, **오직 사용자의 현재 질문에 대해서만 짧고 명확하게 답변**하세요.
            2. 불필요한 다른 분석 내용은 모두 제거하고 핵심 인사이트(Actions) 3가지만 제시하세요.
            3. 점주 권한 내(발주, 진열, 리뷰 등)의 실행 가능한 액션이어야 합니다.
            
            응답은 반드시 아래 JSON 형식을 지켜주세요.
            {{
                "text": "현재 질문에 대한 핵심 요약",
                "evidence": ["질문과 관련된 주요 수치 1-2개"],
                "actions": ["액션 1", "액션 2", "액션 3"],
                "channel_analysis": {json.dumps(cached_channel)},
                "profit_simulation": {json.dumps(cached_profit)}
            }}
            """
            
            try:
                regenerated_json = self.gemini.call_gemini_text(cache_prompt, response_type="application/json")
                reg_data = json.loads(regenerated_json)
                
                insight = SalesInsight(
                    text=reg_data.get("text", ""),
                    evidence=reg_data.get("evidence", []),
                    actions=reg_data.get("actions", [])
                )
                return SalesQueryResponse(
                    answer=insight, 
                    source_data_period="최근 1개월(캐시)",
                    channel_analysis=cached_channel,
                    profit_simulation=cached_profit
                )
            except Exception as e:
                logger.error(f"캐시 재생성 실패, 원본 캐시 반환: {e}")
                return SalesQueryResponse(**cached_result)

        # 캐시 미적중 — 직접 분석 수행
        query_type = self.classifier.classify(payload.query)

        if query_type == "SENSITIVE":
            logger.warning("Sensitive query detected. Blocking response.")
            insight = SalesInsight(
                text="보안 정책에 따라 민감한 경영 정보(상세 원가 등)를 포함한 질문은 직접 분석이 제한됩니다. 표준 마진(30%)을 적용한 시뮬레이션 결과만 제공 가능합니다.",
                evidence=["민감 정보 식별 알고리즘 적용"],
                actions=["보안 대시보드 확인", "표준 마진 시뮬레이션 요청"]
            )
            return SalesQueryResponse(answer=insight, source_data_period="N/A")

        # 의도 파악 및 RAG 매장 프로필 조회
        target_data_type, applied_logic = self.semantic_layer.parse_query_intent(payload.query)
        store_rag_profile = self.rag_service.retrieve_store_profile(payload.store_id)

        # 에이전트를 통해 계산 위임
        channel_res = self.agent.analyze_real_channel_mix(store_id=payload.store_id)
        profit_res = self.agent.simulate_real_profitability(store_id=payload.store_id)
        dynamic_store_profile = self.agent.extract_store_profile(store_id=payload.store_id)
        comparison_res = self.agent.calculate_comparison_metrics(store_id=payload.store_id)
        cross_sell_res = self.agent.extract_cross_sell_combinations(store_id=payload.store_id)

        # 질문 키워드에 따라 분석 포커스 및 우선 데이터 결정
        focus_area = "종합 분석"
        priority_data = ""

        if any(w in payload.query for w in ["배달", "채널", "해피오더", "배민", "쿠팡"]):
            focus_area = "채널 및 결제수단 최적화"
            priority_data = f"특히 [채널 분석 결과]의 배달 비중({channel_res.get('delivery_rate', 0)}%)과 트렌드를 중심으로 분석하세요."
        elif any(w in payload.query for w in ["비교", "성장", "전월", "지난", "주기"]):
            focus_area = "기간별 성장률 비교"
            priority_data = f"특히 [기간 비교 분석 결과]의 매출 성장률({comparison_res.get('growth_rate', 0)}%)과 기간별 차이를 중심으로 분석하세요."
        elif any(w in payload.query for w in ["수익", "마진", "이익", "BEP"]):
            focus_area = "수익성 및 시뮬레이션"
            priority_data = f"특히 [수익성 분석 시뮬레이션]의 추정 마진율({profit_res.get('estimated_margin_rate', 0)*100:.1f}%)을 기반으로 수익 개선안을 도출하세요."
        elif any(w in payload.query for w in ["조합", "같이", "함께", "교차", "세트"]):
            focus_area = "교차 판매 및 메뉴 조합 분석"
            priority_data = "특히 [교차 판매 조합 분석] 데이터를 바탕으로 동반 구매 시너지를 낼 수 있는 전략을 도출하세요."

        enriched_prompt = f"""
        사용자 질문: {payload.query}
        분석 포커스: {focus_area}
        {priority_data}
        
        [매장 최근 판매 패턴 및 수치 특성 (From SQL Database)]
        - 주력 판매 상품(Top 3): {', '.join(dynamic_store_profile.get('top_items', []))}
        - 고객 밀집 피크 시간대: {dynamic_store_profile.get('peak_hour', '')}
        - 음료 동반 구매 비중: {dynamic_store_profile.get('beverage_ratio', 0)}%
        
        [교차 판매 조합 분석 (함께 많이 팔린 상품 Top 5)]
        {json.dumps(cross_sell_res, ensure_ascii=False, indent=2)}
        
        [채널 분석 결과]
        - 배달 비중: {channel_res.get('delivery_rate', 0)}%
        - 트렌드: {channel_res.get('trend', '')}
        - 온라인 매출액: {channel_res.get('online_amt', 0):,.0f}원
        - 오프라인 매출액: {channel_res.get('offline_amt', 0):,.0f}원
        
        [수익성 분석 시뮬레이션 결과 (최근 4주)]
        - 추정 마진율: {profit_res.get('estimated_margin_rate', 0)*100:.1f}%
        - 추정 영업이익: {profit_res.get('estimated_profit', 0):,.0f}원
        - BEP 목표 수량: {profit_res.get('bep_target_qty', 0)}개
        
        [기간 비교 분석 결과 (최근 4주 vs 직전 4주)]
        - 최근 4주 총 매출: {comparison_res.get('recent_4w_sales', 0):,.0f}원
        - 직전 4주 총 매출: {comparison_res.get('previous_4w_sales', 0):,.0f}원
        - 전주기 대비 매출 성장률: {comparison_res.get('growth_rate', 0)}%
        
        [매장 고유 특성 및 상권 정보 (From Vector DB)]
        {store_rag_profile}
        
        **분석 지시사항 (Franchise Compliance 가드레일 필수 적용):**
        1. **질문의 의도({focus_area})에 맞는 데이터를 최우선적으로 사용하여 답변하세요.**
        2. **교차 판매 조합 분석 데이터를 활용할 경우, 실제 함께 잘 팔리는 메뉴 명칭을 언급하며 구체적인 동선/진열 전략을 제안하세요.**
        3. 점주가 당장 실천할 수 있는 매장 맞춤형 액션 아이템 3가지를 도출하세요.
        4. [중요 제약사항] 본 매장은 프랜차이즈 가맹점입니다. 점주 임의의 신규 세트 메뉴 생성, 임의 가격 변경, 사제 할인 쿠폰 발행 등의 제안은 절대 금지합니다.
        5. 발주 관리, 매장 진열(VMD), 포스기 활용, 배달앱 운영 최적화 범주 내에서만 액션을 제안하세요.
        
        응답은 반드시 아래 JSON 형식을 지켜주세요.
        {{
            "text": "질문에 대한 핵심 분석 요약 (함께 팔리는 메뉴들의 마케팅적 의미 포함)",
            "evidence": ["분석의 근거가 된 핵심 수치들 및 주요 조합"],
            "actions": ["실행 가능한 액션 1", "실행 가능한 액션 2", "실행 가능한 액션 3"],
            "channel_analysis": {json.dumps(channel_res)},
            "profit_simulation": {json.dumps(profit_res)}
        }}
        """

        gemini_prompt = create_sales_analysis_prompt(enriched_prompt)
        
        try:
            response_json = self.gemini.call_gemini_text(gemini_prompt, response_type="application/json")
            data = json.loads(response_json)
            
            insight = SalesInsight(
                text=data.get("text", ""),
                evidence=data.get("evidence", []),
                actions=data.get("actions", [])
            )
            final_response = SalesQueryResponse(
                answer=insight, 
                source_data_period="최근 1개월",
                channel_analysis=data.get("channel_analysis"),
                profit_simulation=data.get("profit_simulation")
            )
            
            # 분석 결과 캐시 저장 (원본 질의 포함)
            cache_data = final_response.model_dump(mode='json')
            cache_data["_original_query"] = payload.query
            self.rag_service.save_qa_cache(payload.store_id, payload.query, cache_data)
            
            return final_response
        except Exception as e:
            logger.error(f"Error during sales analysis: {e}")
            insight = SalesInsight(
                text="데이터 분석 중 오류가 발생했습니다.",
                evidence=[str(e)],
                actions=["나중에 다시 시도"]
            )
            return SalesQueryResponse(answer=insight, source_data_period="N/A")

    def extract_store_profile(self, store_id: str, date_from: str, date_to: str) -> dict:
        """API Router에서 요구하는 매장 프로필 추출 메서드 구현"""
        return self.agent.extract_store_profile(store_id)

    def simulate_profitability(
        self, store_id: str, date_from: str, date_to: str
    ) -> ProfitabilitySimulationResponse:
        """표준 마진 기반 수익성 시뮬레이션 (원가 데이터 부재 환경)"""
        _STANDARD_MARGIN = 0.65
        _FALLBACK_REVENUE = 5_000_000.0

        total_revenue = _FALLBACK_REVENUE
        top_items: list = []
        try:
            profile = self.extract_store_profile(store_id, date_from, date_to)
            if profile and isinstance(profile, dict):
                total_revenue = float(profile.get("total_revenue", total_revenue))
                top_items = profile.get("top_items", [])
        except Exception:
            pass

        return ProfitabilitySimulationResponse(
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            total_revenue=total_revenue,
            estimated_margin_rate=_STANDARD_MARGIN,
            estimated_profit=round(total_revenue * _STANDARD_MARGIN),
            top_items=top_items,
            simulation_note="표준 마진 65% 적용 (원가 데이터 부재로 추정값 사용)",
        )
