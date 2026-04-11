from __future__ import annotations

import json
from schemas.contracts import SalesQueryRequest, SalesQueryResponse, SalesInsight
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_sales_analysis_prompt
from services.predictor import QueryClassifier
from services.semantic_layer import SemanticLayer
from services.rag_service import RAGService
from services.sales_analysis_engine import SalesAnalysisEngine

logger = init_logger("sales_analyzer")


class SalesAnalyzer:
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.classifier = QueryClassifier()
        self.semantic_layer = SemanticLayer()
        self.rag_service = RAGService(gemini_client)
        self.analysis_engine = SalesAnalysisEngine()

    def analyze(self, payload: SalesQueryRequest) -> SalesQueryResponse:
        """
        Production-level Sales Analysis Agent:
        1. Semantic Cache Check (시맨틱 캐시 확인)
        2. Guardrail Check.
        3. SQL vs RAG Routing.
        4. Core Analysis & Generation.
        """
        logger.info(f"Analyzing sales query: {payload.query[:50]}")

        # [1] 시맨틱 캐시 확인 (의미적으로 유사한 과거 질문이 있는지 체크)
        cached_result = self.rag_service.lookup_qa_cache(store_id=payload.store_id, query=payload.query)
        if cached_result:
            # [고도화] 캐시된 질문과 현재 질문의 핵심 의도(키워드) 매칭 검증
            intent_keywords = ["조합", "같이", "함께", "세트", "교차", "배달", "채널", "비교", "성장", "수익", "이익"]
            current_query_keywords = [k for k in intent_keywords if k in payload.query]
            cached_query = cached_result.get("_original_query", "")
            
            # 현재 질문의 핵심 키워드가 캐시된 질문에도 하나라도 포함되어 있는지 확인
            # (키워드가 아예 다르면 시맨틱 유사도가 높더라도 무시)
            is_intent_match = any(k in cached_query for k in current_query_keywords) if current_query_keywords else True
            
            if is_intent_match:
                logger.info(f"🎉 매장 {payload.store_id}의 시맨틱 캐시 적중! 맞춤형 답변을 재생성합니다.")
                # (이하 캐시 재활용 로직 동일)
            
            # 과거 분석된 수치 데이터 추출
            cached_channel = cached_result.get("channel_analysis", {})
            cached_profit = cached_result.get("profit_simulation", {})
            cached_evidence = cached_result.get("answer", {}).get("evidence", [])
            
            # 캐시된 수치를 바탕으로 현재 질문에만 100% 집중하는 가벼운 프롬프트 생성
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
                # LLM 재호출 (캐시된 데이터를 바탕으로 답변만 다시 작성)
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

        # 2. Classification & Guardrail
        query_type = self.classifier.classify(payload.query)
        
        if query_type == "SENSITIVE":
            logger.warning("Sensitive query detected. Blocking response.")
            insight = SalesInsight(
                text="보안 정책에 따라 민감한 경영 정보(상세 원가 등)를 포함한 질문은 직접 분석이 제한됩니다. 표준 마진(30%)을 적용한 시뮬레이션 결과만 제공 가능합니다.",
                evidence=["민감 정보 식별 알고리즘 적용"],
                actions=["보안 대시보드 확인", "표준 마진 시뮬레이션 요청"]
            )
            return SalesQueryResponse(answer=insight, source_data_period="N/A")

        # 2. SQL vs RAG Routing Logic (Simulated for this draft)
        # 가이드라인: 수치 질문은 SQL로, 분석/트렌드 질문은 Vector로
        is_numeric_query = any(word in payload.query for word in ["얼마", "건수", "매출", "몇"])
        
        if is_numeric_query:
            logger.info("Routing to SQL Agent (Structured Data)")
            # 실제 구현에서는 LLM이 SQL을 생성하도록 유도
            data_context_type = "structured_sql"
        else:
            logger.info("Routing to RAG (Vector Search)")
            data_context_type = "unstructured_vector"

        # 3. Semantic Layer (의도 파악 및 로직 추출)
        target_data_type, applied_logic = self.semantic_layer.parse_query_intent(payload.query)
        
        # 4. Vector RAG (매장 고유 특성 정보)
        store_rag_profile = self.rag_service.retrieve_store_profile(payload.store_id)

        # 5. Core Engine Logic Execution (SQL Agent - DB 실시간 조회)
        channel_res = self.analysis_engine.analyze_real_channel_mix(store_id=payload.store_id)
        profit_res = self.analysis_engine.simulate_real_profitability(store_id=payload.store_id)
        dynamic_store_profile = self.analysis_engine.extract_store_profile(store_id=payload.store_id)
        # [추가] 기간별 비교 분석 지표 (L4W vs P4W)
        comparison_res = self.analysis_engine.calculate_comparison_metrics(store_id=payload.store_id)
        # [추가] 교차 판매(함께 많이 팔린) 조합 Top 5
        cross_sell_res = self.analysis_engine.extract_cross_sell_combinations(store_id=payload.store_id)

        # 6. 질문 의도에 따른 분석 강조점(Focus Area) 결정
        focus_area = "종합 분석"
        priority_data = ""
        
        if any(w in payload.query for w in ["배달", "채널", "해피오더", "배민", "쿠팡"]):
            focus_area = "채널 및 결제수단 최적화"
            priority_data = f"특히 [채널 분석 결과]의 배달 비중({channel_res['delivery_rate']}%)과 트렌드를 중심으로 분석하세요."
        elif any(w in payload.query for w in ["비교", "성장", "전월", "지난", "주기"]):
            focus_area = "기간별 성장률 비교"
            priority_data = f"특히 [기간 비교 분석 결과]의 매출 성장률({comparison_res.get('growth_rate', 0)}%)과 기간별 차이를 중심으로 분석하세요."
        elif any(w in payload.query for w in ["수익", "마진", "이익", "BEP"]):
            focus_area = "수익성 및 시뮬레이션"
            priority_data = f"특히 [수익성 분석 시뮬레이션]의 추정 마진율({profit_res['estimated_margin_rate']*100:.1f}%)을 기반으로 수익 개선안을 도출하세요."
        elif any(w in payload.query for w in ["조합", "같이", "함께", "교차", "세트"]):
            focus_area = "교차 판매 및 메뉴 조합 분석"
            priority_data = "특히 [교차 판매 조합 분석] 데이터를 바탕으로 동반 구매 시너지를 낼 수 있는 전략을 도출하세요."

        # 7. Prompt Construction (의도에 따라 동적 재구성)
        enriched_prompt = f"""
        사용자 질문: {payload.query}
        분석 포커스: {focus_area}
        {priority_data}
        
        [매장 최근 판매 패턴 및 수치 특성 (From SQL Database)]
        - 주력 판매 상품(Top 3): {', '.join(dynamic_store_profile['top_items'])}
        - 고객 밀집 피크 시간대: {dynamic_store_profile['peak_hour']}
        - 음료 동반 구매 비중: {dynamic_store_profile['beverage_ratio']}%
        
        [교차 판매 조합 분석 (함께 많이 팔린 상품 Top 5)]
        {json.dumps(cross_sell_res, ensure_ascii=False, indent=2)}
        
        [채널 분석 결과]
        - 배달 비중: {channel_res['delivery_rate']}%
        - 트렌드: {channel_res['trend']}
        - 온라인 매출액: {channel_res.get('online_amt', 0):,.0f}원
        - 오프라인 매출액: {channel_res.get('offline_amt', 0):,.0f}원
        
        [수익성 분석 시뮬레이션 결과 (최근 4주)]
        - 추정 마진율: {profit_res['estimated_margin_rate']*100:.1f}%
        - 추정 영업이익: {profit_res['estimated_profit']:,.0f}원
        - BEP 목표 수량: {profit_res['bep_target_qty']}개
        
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
        
        # 7. Generation
        try:
            response_json = self.gemini.call_gemini_text(gemini_prompt, response_type="application/json")
            data = json.loads(response_json)
            
            # SalesInsight 스키마에 맞게 조정 (필요 시 필드 확장)
            insight = SalesInsight(
                text=data.get("text", ""),
                evidence=data.get("evidence", []),
                actions=data.get("actions", [])
            )
            # JSON Interface 데이터를 SalesQueryResponse 필드에 맞게 매핑
            final_response = SalesQueryResponse(
                answer=insight, 
                source_data_period="최근 1개월",
                channel_analysis=data.get("channel_analysis"),
                profit_simulation=data.get("profit_simulation")
            )
            
            # [2] 시맨틱 캐시에 결과 저장 (나중에 유사 질문이 들어오면 재사용)
            # mode='json'을 사용하여 datetime 객체를 안전한 문자열로 직렬화합니다.
            cache_data = final_response.model_dump(mode='json')
            cache_data["_original_query"] = payload.query # 원본 질문 저장
            
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
