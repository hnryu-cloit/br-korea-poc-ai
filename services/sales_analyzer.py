from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from common.gemini import Gemini
from schemas.contracts import (
    ProfitabilitySimulationResponse,
    SalesInsight,
    SalesPromptItem,
    SalesPromptSuggestRequest,
    SalesPromptSuggestResponse,
    SalesQueryRequest,
    SalesQueryResponse,
)
from services.sales_agent import SalesAnalysisAgent
from services.grounded_workflow import GroundedWorkflow

logger = logging.getLogger(__name__)


# 매출 분석기 전용 차단 경로에서 사용하는 레거시 헬퍼
class QueryClassifier:
    """민감 키워드를 식별하여 차단하는 역할"""

    SENSITIVE_WORDS = ["원가", "마진율", "영업비밀", "타점포 매출", "이익률", "마진"]

    @classmethod
    def check_sensitive(cls, query: str) -> bool:
        for word in cls.SENSITIVE_WORDS:
            if word in query:
                return True
        return False


# 모델에 필요한 테이블을 질의하는 레거시 헬퍼
class SemanticLayer:
    """질의 유형을 분류하고 관련 테이블 힌트를 선택하는 역할"""

    def __init__(self, gemini: Gemini, schema_context: str):
        self.gemini = gemini
        self.schema_context = schema_context

    def get_routing_hints(self, query: str, system_instruction: str | None = None) -> list[str]:
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
            res_str = self.gemini.call_gemini_text(
                prompt,
                system_instruction=system_instruction,
                response_type="application/json",
            )
            data = json.loads(res_str)
            return data.get("required_tables", ["DAILY_STOR_ITEM"])
        except Exception as e:
            logger.error(f"SemanticLayer 오류: {e}")
            return ["DAILY_STOR_ITEM"]


# 구형 분석기 경로에서 사용하는 레거시 SQL 생성기
class SQLGenerator:
    """LLM + 스키마 컨텍스트를 활용하여 SELECT SQL을 생성하는 역할"""

    def __init__(self, gemini: Gemini, schema_context: str):
        self.gemini = gemini
        self.schema_context = schema_context

    def generate(
        self, query: str, target_tables: list[str], system_instruction: str | None = None
    ) -> str:
        prompt = f"""
        다음 데이터베이스 스키마와 사용자의 질문을 바탕으로 실행 가능한 PostgreSQL SELECT 쿼리를 작성하세요.
        
        - [필수] 조회 대상 매장 코드는 파라미터 바인딩을 위해 반드시 `:store_id` 로 작성하세요. (예: WHERE masked_stor_cd = :store_id)
        - [필수] 오직 'SELECT' 쿼리만 작성해야 합니다. (UPDATE, DELETE 등 불가)
        - [필수] sale_dt는 TEXT(YYYYMMDD) 입니다. 숫자형으로 가정하지 마세요.
        - [필수] sale_dt와 숫자 리터럴을 직접 비교하지 마세요. 날짜 비교는 문자열 비교 또는 명시적 CAST를 사용하세요.
          예1) sale_dt >= '20240401'
          예2) CAST(sale_dt AS BIGINT) >= 20240401
        - [필수] sale_dt에 직접 산술연산 금지 (예: sale_dt - 6 금지). 필요한 경우 CAST 후 연산하세요.
          예) CAST(sale_dt AS BIGINT) - 6
        - [필수] 테이블명과 컬럼명은 영문 소문자로 작성하고 쌍따옴표를 쓰지 마세요.
        - [필수] sale_amt, sale_qty 등 계산이 필요한 컬럼은 DB에서 텍스트(text)로 저장되어 있을 수 있으므로 합계를 구할 때 반드시 명시적으로 CAST 함수를 사용해야 합니다. (예: SUM(CAST(sale_amt AS NUMERIC)))
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
            res_str = self.gemini.call_gemini_text(
                prompt,
                system_instruction=system_instruction,
                response_type="application/json",
            )
            data = json.loads(res_str)
            return data.get("sql", "")
        except Exception as e:
            logger.error(f"SQLGenerator 오류: {e}")
            return "SELECT item_nm, SUM(CAST(sale_qty AS NUMERIC)) AS total_qty FROM raw_daily_store_item WHERE masked_stor_cd = :store_id GROUP BY item_nm ORDER BY total_qty DESC LIMIT 5"


# 매출 에이전트를 통해 검증된 읽기 전용 SQL 실행
class QueryExecutor:
    """실제 PostgreSQL 조회를 담당하는 역할 (안전성 검증 포함)"""

    def __init__(self, agent: SalesAnalysisAgent):
        self.agent = agent

    def execute(self, store_id: str, sql: str, target_tables: list[str]) -> list[dict[str, Any]]:
        # 안전성 검증
        if not sql.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed.")

        # Agent의 동적 쿼리 실행기 활용 (내부적으로 QueryLogger 기록됨)
        return self.agent.execute_dynamic_sql(store_id, sql, target_tables)


# 실제 조회 행을 바탕으로 사용자 대상 요약·근거·액션 생성
class GroundedAnalyzer:
    """LLM이 실제 조회된 데이터(Row)를 바탕으로 답변을 생성하는 역할"""

    def __init__(self, gemini: Gemini):
        self.gemini = gemini

    def analyze(
        self,
        query: str,
        raw_data: list[dict[str, Any]],
        sql_query: str,
        system_instruction: str | None = None,
    ) -> tuple[str, list[str], list[str]]:
        def default_serializer(obj):
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
            res_str = self.gemini.call_gemini_text(
                prompt,
                system_instruction=system_instruction,
                response_type="application/json",
            )
            data = json.loads(res_str)
            return data.get("text", "분석 완료"), data.get("evidence", []), data.get("actions", [])
        except Exception as e:
            logger.error(f"GroundedAnalyzer 오류: {e}")
            return (
                "데이터를 분석했으나 상세 인사이트를 생성하는 데 문제가 발생했습니다.",
                ["조회는 성공했으나 요약에 실패함"],
                ["다시 시도해주세요"],
            )


class SalesAnalyzer:
    """
    사용자의 자연어 질문을 입력받아, 데이터베이스에서 실제 데이터를 조회(Grounded)하고,
    이를 바탕으로 정확한 답변과 인사이트를 생성하는 매출 분석 AI 오케스트레이터.
    """

    def __init__(self, gemini_client: Gemini):
        self.gemini = gemini_client
        self.agent = SalesAnalysisAgent()

        # Legacy pipeline components are kept for compatibility, but
        # current natural-language questions are routed through GroundedWorkflow.
        self.semantic_layer = SemanticLayer(self.gemini, self.agent.get_schema_context())
        self.sql_generator = SQLGenerator(self.gemini, self.agent.get_schema_context())
        self.query_executor = QueryExecutor(self.agent)
        self.grounded_analyzer = GroundedAnalyzer(self.gemini)

    def analyze(self, payload: SalesQueryRequest) -> SalesQueryResponse:
        # Main entrypoint for the sales analysis agent.
        # It blocks sensitive questions first, then delegates grounded answering.
        logger.info(f"Pipeline Start for query: {payload.query[:50]}")
        store_id = payload.store_id
        user_query = payload.query
        system_instruction = payload.system_instruction

        # 1. QueryClassifier (민감 키워드 차단)
        if QueryClassifier.check_sensitive(user_query):
            logger.warning("Sensitive query detected and blocked.")
            insight = SalesInsight(
                text="보안 정책에 따라 원가/마진 등 민감한 경영 정보는 조회가 제한됩니다.",
                evidence=["민감 키워드 감지 필터 작동"],
                actions=["일반 매출 및 채널 현황 분석으로 다시 질문해 주세요."],
            )
            return SalesQueryResponse(answer=insight, source_data_period="N/A", data_lineage=[])

        workflow = GroundedWorkflow(self.gemini)
        result = workflow.run(
            query=user_query,
            store_id=store_id,
            domain=payload.domain or "sales",
            reference_date=payload.business_date,
        )
        # Normalize grounded workflow output into the public API contract.
        return {
            "answer": {
                "text": result.get("text", ""),
                "evidence": result.get("evidence", []),
                "actions": result.get("actions", []),
            },
            "source_data_period": "실시간 DB 연동 (Grounded Analysis)",
            "request_context": {
                "store_id": payload.store_id,
                "business_date": payload.business_date,
                "business_time": getattr(payload, "business_time", None),
                "prompt": payload.query,
                "domain": payload.domain or "sales",
            },
            "queried_period": result.get("queried_period"),
            "grounding": {
                "keywords": result.get("keywords", []),
                "intent": result.get("intent"),
                "relevant_tables": result.get("relevant_tables", []),
                "sql": result.get("sql"),
                "row_count": result.get("row_count", 0),
            },
            "data_lineage": result.get("data_lineage", []),
        }

        # 2. SemanticLayer (질의 유형 분류 및 테이블 힌트)
        # 여기서 intent를 명시적으로 가져오도록 수정
        prompt = f"""
        당신은 프랜차이즈 데이터 분석 라우터입니다.
        사용자의 질문을 분석하여, 의도(Intent)와 필요한 테이블을 결정하세요.
        
        [사용자 질문]
        {user_query}
        
        [데이터베이스 스키마 정의]
        {self.agent.get_schema_context()}
        
        반드시 다음 JSON 형식으로만 응답하세요:
        {{
            "intent_category": "channel | campaign | sales_trend | cross_sell | other",
            "required_tables": ["테이블명1", "테이블명2"]
        }}
        """
        intent = "other"
        target_tables = ["raw_daily_store_item"]
        try:
            res_str = self.gemini.call_gemini_text(
                prompt,
                system_instruction=system_instruction,
                response_type="application/json",
            )
            data = json.loads(res_str)
            intent = data.get("intent_category", "other")
            target_tables = data.get("required_tables", ["raw_daily_store_item"])
        except Exception as e:
            logger.error(f"Semantic routing 오류: {e}")

        # 3. 특수 인텐트 처리 (Cross-sell)
        if intent == "cross_sell":
            logger.info("Cross-sell intent detected. Running association analysis.")
            raw_data = self.agent.analyze_cross_sell(store_id)
            sql_query = "INTERNAL ASSOCIATION RULE MINING (ORD_DTL)"
        else:
            # 4. SQLGenerator (LLM + 스키마 -> SELECT SQL)
            sql_query = self.sql_generator.generate(
                user_query,
                target_tables,
                system_instruction=system_instruction,
            )
            logger.info(f"Generated SQL: {sql_query}")

            # 5. QueryExecutor (실제 데이터 조회)
            try:
                raw_data = self.query_executor.execute(store_id, sql_query, target_tables)
            except Exception as e:
                logger.error(f"Execution failed: {e}")
                raw_data = [{"error": str(e)}]

        # 6. GroundedAnalyzer (LLM이 데이터 보고 답변 생성)
        text, evidence, actions = self.grounded_analyzer.analyze(
            user_query,
            raw_data,
            sql_query,
            system_instruction=system_instruction,
        )

        insight = SalesInsight(text=text, evidence=evidence, actions=actions)

        # 7. SalesQueryResponse 구성 (데이터 리니지 포함)
        lineage = self.agent.get_data_lineage()

        return SalesQueryResponse(
            answer=insight,
            source_data_period="실시간 DB 연동 (Grounded Analysis)",
            data_lineage=lineage,
        )

    def suggest_prompts(self, payload: SalesPromptSuggestRequest) -> SalesPromptSuggestResponse:
        context_json = json.dumps(payload.context_prompts, ensure_ascii=False)
        prompt = f"""
        당신은 매장 운영 데이터 분석 코치입니다.
        아래 컨텍스트를 참고해 점주가 바로 눌러볼 추천 질문 5개를 만들어 주세요.

        [도메인]
        {payload.domain}

        [점포]
        {payload.store_id}

        [데이터 기반 컨텍스트 초안 질문]
        {context_json}

        규칙:
        1) 반드시 점포 데이터 맥락이 드러나는 질문만 생성
        2) 중복/유사 질문 금지
        3) 실행 가능한 질문 우선
        4) label은 짧게, prompt는 완전한 질문으로
        5) 결과는 최대 5개

        JSON 형식:
        {{
          "prompts": [
            {{"label": "짧은 라벨", "category": "domain", "prompt": "질문 문장"}}
          ]
        }}
        """
        try:
            res_str = self.gemini.call_gemini_text(
                prompt,
                system_instruction=payload.system_instruction,
                response_type="application/json",
            )
            data = json.loads(res_str)
            items = data.get("prompts", [])
            prompts = [
                SalesPromptItem(
                    label=str(item.get("label", "")).strip(),
                    category=str(item.get("category", payload.domain)).strip() or payload.domain,
                    prompt=str(item.get("prompt", "")).strip(),
                )
                for item in items
                if isinstance(item, dict)
                and str(item.get("label", "")).strip()
                and str(item.get("prompt", "")).strip()
            ][:5]
            return SalesPromptSuggestResponse(
                store_id=payload.store_id, domain=payload.domain, prompts=prompts
            )
        except Exception as exc:
            logger.error("추천 질문 생성 실패: %s", exc)
            return SalesPromptSuggestResponse(
                store_id=payload.store_id,
                domain=payload.domain,
                prompts=[],
            )

    def simulate_profitability(
        self, store_id: str, date_from: str, date_to: str
    ) -> ProfitabilitySimulationResponse:
        """실제 DB 매출 기반 수익성 시뮬레이션 수행"""

        # 1. Agent를 통한 실제 통계 계산
        real_stats = self.agent.simulate_real_profitability(store_id)
        if "error" in real_stats:
            raise ValueError(real_stats["error"])

        comparison = self.agent.calculate_comparison_metrics(store_id)
        store_profile = self.agent.extract_store_profile(store_id)

        # 2. 결과 구성
        return ProfitabilitySimulationResponse(
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            total_revenue=real_stats["total_sales"],
            estimated_margin_rate=real_stats["estimated_margin_rate"],
            estimated_profit=real_stats["estimated_profit"],
            top_items=[
                {"name": item, "rank": i + 1}
                for i, item in enumerate(store_profile.get("top_items", []))
            ],
            simulation_note=f"최근 성장률 {comparison.get('growth_rate', 0)}% 기반 분석 결과입니다.",
        )
