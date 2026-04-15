from __future__ import annotations
import json
import logging
from typing import List, Dict, Any, Tuple
from datetime import datetime

from schemas.contracts import SalesQueryRequest, SalesQueryResponse, SalesInsight
from common.gemini import Gemini
from services.sales_agent import SalesAnalysisAgent

logger = logging.getLogger("sales_analyzer")

class QueryClassifier:
    """민감 키워드를 식별하여 차단하는 역할"""
    SENSITIVE_WORDS = ["원가", "마진율", "영업비밀", "타점포 매출", "이익률", "마진"]
    
    @classmethod
    def check_sensitive(cls, query: str) -> bool:
        for word in cls.SENSITIVE_WORDS:
            if word in query:
                return True
        return False

class SemanticLayer:
    """질의 유형을 분류하고 관련 테이블 힌트를 선택하는 역할"""
    def __init__(self, gemini: Gemini, schema_context: str):
        self.gemini = gemini
        self.schema_context = schema_context

    def get_routing_hints(self, query: str) -> List[str]:
        prompt = f"""
        당신은 프랜차이즈 데이터 분석 라우터입니다.
        사용자의 질문을 분석하여, 다음 데이터베이스 스키마 중 어떤 테이블을 조회해야 할지 결정하세요.
        
        [사용자 질문]
        {query}
        
        [데이터베이스 스키마 정의]
        {self.schema_context}
        
        반드시 다음 JSON 형식으로만 응답하세요:
        {{
            "intent_category": "channel | campaign | sales_trend | cross_sell",
            "required_tables": ["테이블명1", "테이블명2"]
        }}
        """
        try:
            res_str = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(res_str)
            return data.get("required_tables", ["DAILY_STOR_ITEM"])
        except Exception as e:
            logger.error(f"SemanticLayer 오류: {e}")
            return ["DAILY_STOR_ITEM"]

class SQLGenerator:
    """LLM + 스키마 컨텍스트를 활용하여 SELECT SQL을 생성하는 역할"""
    def __init__(self, gemini: Gemini, schema_context: str):
        self.gemini = gemini
        self.schema_context = schema_context

    def generate(self, query: str, target_tables: List[str]) -> str:
        prompt = f"""
        다음 데이터베이스 스키마와 사용자의 질문을 바탕으로 실행 가능한 PostgreSQL SELECT 쿼리를 작성하세요.
        
        - [필수] 조회 대상 매장 코드는 파라미터 바인딩을 위해 반드시 `:store_id` 로 작성하세요. (예: WHERE "MASKED_STOR_CD" = :store_id)
        - [필수] 오직 'SELECT' 쿼리만 작성해야 합니다. (UPDATE, DELETE 등 불가)
        - [필수] SALE_DT는 bigint(숫자형)입니다. 문자열 비교 함수(LIKE, substring 등)를 사용하지 말고 크기 비교(>=, <=)를 사용하세요. (예: "SALE_DT" >= 20240401)
        - 테이블명과 컬럼명은 반드시 쌍따옴표(")로 감싸주세요.
        - 기간 조건이 명시되지 않았다면, 최근 데이터 조회를 가정하고 LIMIT 10 등을 사용해 데이터를 제한하세요.

        [사용자 질문]
        {query}
        
        [사용할 테이블 목록]
        {target_tables}
        
        [데이터베이스 스키마 정의]
        {self.schema_context}
        
        반드시 다음 JSON 형식으로 응답하세요:
        {{
            "sql": "실행 가능한 SELECT 쿼리"
        }}
        """
        try:
            res_str = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(res_str)
            return data.get("sql", "")
        except Exception as e:
            logger.error(f"SQLGenerator 오류: {e}")
            return 'SELECT "ITEM_NM", SUM(CAST("SALE_QTY" AS NUMERIC)) as total_qty FROM "DAILY_STOR_ITEM" WHERE "MASKED_STOR_CD" = :store_id GROUP BY "ITEM_NM" ORDER BY total_qty DESC LIMIT 5'

class QueryExecutor:
    """실제 PostgreSQL 조회를 담당하는 역할 (안전성 검증 포함)"""
    def __init__(self, agent: SalesAnalysisAgent):
        self.agent = agent

    def execute(self, store_id: str, sql: str, target_tables: List[str]) -> List[Dict[str, Any]]:
        # 안전성 검증
        if not sql.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed.")
            
        # Agent의 동적 쿼리 실행기 활용 (내부적으로 QueryLogger 기록됨)
        return self.agent.execute_dynamic_sql(store_id, sql, target_tables)

class GroundedAnalyzer:
    """LLM이 실제 조회된 데이터(Row)를 바탕으로 답변을 생성하는 역할"""
    def __init__(self, gemini: Gemini):
        self.gemini = gemini

    def analyze(self, query: str, raw_data: List[Dict[str, Any]], sql_query: str) -> Tuple[str, List[str], List[str]]:
        def default_serializer(obj):
            from decimal import Decimal
            if isinstance(obj, Decimal):
                return float(obj)
            raise TypeError(f"Type {type(obj)} not serializable")
            
        prompt = f"""
        당신은 프랜차이즈 매장 점주를 돕는 AI 비서입니다.
        아래는 사용자의 질문에 대해 실제 데이터베이스를 조회한 결과입니다.
        이를 바탕으로 점주가 이해하기 쉬운 자연어 인사이트를 제공하세요.
        
        [사용자 질문]
        {query}
        
        [실행된 SQL 쿼리]
        {sql_query}
        
        [실제 데이터 조회 결과]
        {json.dumps(raw_data, ensure_ascii=False, default=default_serializer)[:3000]}
        
        [요청 사항]
        1. text: 조회 결과를 바탕으로 한 요약 설명
        2. evidence: SQL 조회 결과를 근거로 작성된 문장 배열 (반드시 구체적인 수치와 함께 작성. '근거 N: ~' 형태)
        3. actions: 점주가 즉시 실행 가능한 액션 아이템 배열
        
        응답은 반드시 아래 JSON 형식을 지켜주세요:
        {{
            "text": "요약 설명",
            "evidence": [
                "근거 1: 최근 28일 배달 매출 2,340,000원 (전체 10,050,000원의 23.3%)",
                "근거 2: 요기요 비중 12.1%, 배달의민족 8.4%"
            ],
            "actions": ["액션 1", "액션 2"]
        }}
        """
        try:
            res_str = self.gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(res_str)
            return data.get("text", "분석 완료"), data.get("evidence", []), data.get("actions", [])
        except Exception as e:
            logger.error(f"GroundedAnalyzer 오류: {e}")
            return "데이터를 분석했으나 상세 인사이트를 생성하는 데 문제가 발생했습니다.", ["조회는 성공했으나 요약에 실패함"], ["다시 시도해주세요"]


class SalesAnalyzer:
    """
    사용자의 자연어 질문을 입력받아, 데이터베이스에서 실제 데이터를 조회(Grounded)하고,
    이를 바탕으로 정확한 답변과 인사이트를 생성하는 매출 분석 AI 오케스트레이터.
    """
    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.agent = SalesAnalysisAgent()
        
        # Pipeline Components
        self.semantic_layer = SemanticLayer(self.gemini, self.agent.get_schema_context())
        self.sql_generator = SQLGenerator(self.gemini, self.agent.get_schema_context())
        self.query_executor = QueryExecutor(self.agent)
        self.grounded_analyzer = GroundedAnalyzer(self.gemini)

    def analyze(self, payload: SalesQueryRequest) -> SalesQueryResponse:
        logger.info(f"Pipeline Start for query: {payload.query[:50]}")
        store_id = payload.store_id
        user_query = payload.query

        # 1. QueryClassifier (민감 키워드 차단)
        if QueryClassifier.check_sensitive(user_query):
            logger.warning("Sensitive query detected and blocked.")
            insight = SalesInsight(
                text="보안 정책에 따라 원가/마진 등 민감한 경영 정보는 조회가 제한됩니다.",
                evidence=["민감 키워드 감지 필터 작동"],
                actions=["일반 매출 및 채널 현황 분석으로 다시 질문해 주세요."]
            )
            return SalesQueryResponse(answer=insight, source_data_period="N/A", data_lineage=[])

        # 2. SemanticLayer (질의 유형 분류 및 테이블 힌트)
        target_tables = self.semantic_layer.get_routing_hints(user_query)
        logger.info(f"Target Tables selected: {target_tables}")

        # 3. SQLGenerator (LLM + 스키마 -> SELECT SQL)
        sql_query = self.sql_generator.generate(user_query, target_tables)
        logger.info(f"Generated SQL: {sql_query}")

        # 4. QueryExecutor (실제 데이터 조회)
        try:
            raw_data = self.query_executor.execute(store_id, sql_query, target_tables)
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            raw_data = [{"error": str(e)}]

        # 5. GroundedAnalyzer (LLM이 데이터 보고 답변 생성)
        text, evidence, actions = self.grounded_analyzer.analyze(user_query, raw_data, sql_query)
        
        insight = SalesInsight(
            text=text,
            evidence=evidence,
            actions=actions
        )

        # 6. SalesQueryResponse 구성 (데이터 리니지 포함)
        lineage = self.agent.get_data_lineage()
        
        return SalesQueryResponse(
            answer=insight,
            source_data_period="실시간 DB 연동 (Grounded Analysis)",
            data_lineage=lineage
        )

    def simulate_profitability(self, store_id: str, date_from: str, date_to: str) -> ProfitabilitySimulationResponse:
        """실제 DB 매출 기반 수익성 시뮬레이션 수행"""
        from schemas.contracts import ProfitabilitySimulationResponse
        
        # 1. Agent를 통한 실제 통계 계산
        real_stats = self.agent.simulate_real_profitability(store_id)
        comparison = self.agent.calculate_comparison_metrics(store_id)
        store_profile = self.agent.extract_store_profile(store_id)
        
        # 2. 결과 구성
        return ProfitabilitySimulationResponse(
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            total_revenue=real_stats.get("total_sales", 0.0),
            estimated_margin_rate=real_stats.get("estimated_margin_rate", 0.65),
            estimated_profit=real_stats.get("estimated_profit", 0.0),
            top_items=[{"name": item, "rank": i+1} for i, item in enumerate(store_profile.get("top_items", []))],
            simulation_note=f"최근 성장률 {comparison.get('growth_rate', 0)}% 기반 분석 결과입니다."
        )
