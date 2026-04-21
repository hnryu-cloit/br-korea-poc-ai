from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from common.gemini import Gemini

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SchemaRegistry (표준 뷰 중심)
# ---------------------------------------------------------------------------

_SCHEMA: dict = {
    "DAILY_STOR_ITEM": {
        "description": "일별 매장-상품 매출 집계 (가장 기본적인 매출 원천)",
        "columns": {
            "MASKED_STOR_CD": "매장 코드 (필터 기준)",
            "SALE_DT": "판매일 (BIGINT 형식, 예: 20260415. 비교 시 20260401 형식 사용)",
            "ITEM_NM": "상품명 (예: '소금 우유 도넛', '아메리카노')",
            "SALE_AMT": "매출액 (NUMERIC)",
            "SALE_QTY": "판매 수량 (NUMERIC)",
        },
        "notes": "매출, 인기 상품, 기간별 판매 추이 분석 시 최우선 사용",
    },
    "PROD_DTL": {
        "description": "상품 생산 이력 (매장에서 직접 제조하는 상품의 생산량)",
        "columns": {
            "MASKED_STOR_CD": "매장 코드",
            "SALE_DT": "생산일 (YYYYMMDD 형식 문자열)",
            "ITEM_NM": "상품명",
            "PROD_QTY": "1차 생산 수량 (NUMERIC)",
            "PROD_QTY_2": "2차 생산 수량 (NUMERIC)",
            "PROD_QTY_3": "3차 생산 수량 (NUMERIC)",
        },
        "notes": "총 생산량 = COALESCE(PROD_QTY,0) + COALESCE(PROD_QTY_2,0) + COALESCE(PROD_QTY_3,0)",
    },
    "ORD_DTL": {
        "description": "매장 발주(주문) 이력",
        "columns": {
            "MASKED_STOR_CD": "매장 코드",
            "SALE_DT": "배송 예정일 (YYYYMMDD 형식 문자열)",
            "ITEM_NM": "상품명",
            "ORD_QTY": "점주가 주문한 수량 (NUMERIC)",
            "ORD_AMT": "주문 금액 (NUMERIC)",
        },
        "notes": "매장 영업을 위해 본사에 주문한 내역 분석 시 사용",
    },
    "SPL_DAY_STOCK_DTL": {
        "description": "매장 재고 이력",
        "columns": {
            "MASKED_STOR_CD": "매장 코드",
            "SALE_DT": "재고 기준일 (YYYYMMDD 형식 문자열)",
            "ITEM_NM": "상품명",
            "STOCK_QTY": "현재고 수량 (NUMERIC)",
        },
        "notes": "특정 시점의 재고 보유량 조회 시 사용",
    },
}

_TABLE_HINTS: dict[str, list[str]] = {
    "sales": ["DAILY_STOR_ITEM"],
    "production": ["PROD_DTL", "DAILY_STOR_ITEM"],
    "order": ["ORD_DTL"],
    "inventory": ["SPL_DAY_STOCK_DTL", "PROD_DTL", "DAILY_STOR_ITEM"],
    "general": ["DAILY_STOR_ITEM", "PROD_DTL", "ORD_DTL"],
}

_FEW_SHOT_EXAMPLES = """
[날짜 계산 규칙 예시]
오늘 날짜가 2026-03-10인 경우:
- '어제': 20260309
- '최근 3일간': 20260307 ~ 20260309 (오늘인 10일은 제외!)
- '지난주': 20260302 ~ 20260308

[질의 예시]
질문: 최근 3일간 평균 생산량 알려줘 (기준일: 2026-03-10)
SQL: SELECT AVG(COALESCE("PROD_QTY",0) + COALESCE("PROD_QTY_2",0) + COALESCE("PROD_QTY_3",0)) as avg_prod FROM "PROD_DTL" WHERE "MASKED_STOR_CD" = :store_id AND "SALE_DT" BETWEEN '20260307' AND '20260309';

질문: 페이머스글레이즈드 최근 7일 재고 흐름 보여줘 (기준일: 2026-03-10)
SQL: SELECT "SALE_DT", "STOCK_QTY" FROM "SPL_DAY_STOCK_DTL" WHERE "MASKED_STOR_CD" = :store_id AND "ITEM_NM" LIKE '%페이머스%글레이즈드%' AND "SALE_DT" BETWEEN '20260303' AND '20260309' ORDER BY "SALE_DT" ASC;
"""

def get_schema_context(table_names: list[str] | None = None) -> str:
    targets = table_names or list(_SCHEMA.keys())
    lines = []
    for tbl in targets:
        if tbl not in _SCHEMA: continue
        meta = _SCHEMA[tbl]
        lines.append(f"### {tbl}")
        lines.append(f"설명: {meta['description']}")
        lines.append("컬럼:")
        for col, desc in meta["columns"].items():
            lines.append(f"  - {col}: {desc}")
        lines.append("")
    return "\n".join(lines)

def get_table_hints(query_type: str) -> list[str]:
    return _TABLE_HINTS.get(query_type, _TABLE_HINTS["general"])

# ---------------------------------------------------------------------------
# SQLGenerator
# ---------------------------------------------------------------------------

_SQL_SYSTEM = """\
당신은 한국 베이커리 프랜차이즈 분석 시스템의 PostgreSQL 전문가입니다.

[절대 규칙 - 날짜 계산]
1. 모든 '최근 n일', '주간', '월간' 집계 시 **오늘(기준일)은 제외**한다.
2. 집계 대상은 항상 **영업이 종료된 어제(기준일 - 1일)까지**로 제한한다.
   - 예: 오늘이 10일이면 '최근 3일'은 7일, 8일, 9일이다.
3. CURRENT_DATE 함수를 쓰지 말고, 프롬프트의 [기준일]을 사용해 직접 계산한 날짜 문자열을 사용한다.

[기본 규칙]
1. 'raw_' 테이블 금지. 반드시 "PROD_DTL", "ORD_DTL", "DAILY_STOR_ITEM", "SPL_DAY_STOCK_DTL"만 사용.
2. 테이블/컬럼명은 쌍따옴표(") 필수.
3. 항상 "MASKED_STOR_CD" = :store_id 필터 포함.
4. 결과는 JSON으로만 응답.
"""

@dataclass
class GeneratedSQL:
    sql: str
    description: str
    relevant_tables: list[str]

class SQLGenerator:
    def __init__(self, gemini: Gemini) -> None:
        self.gemini = gemini

    def generate(self, query: str, store_id: str, query_type: str = "general") -> GeneratedSQL:
        table_hints = get_table_hints(query_type)
        schema_context = get_schema_context(table_hints)
        today_ref = "2026-03-10" # 테스트 고정 기준일

        prompt = f"""[기준일] {today_ref} (오늘 영업 중)
[DB 스키마]
{schema_context}

[날짜 가이드] 최근 n일 조회 시 오늘({today_ref})을 제외하고 어제부터 과거로 n일간의 범위를 잡으세요.

[참고 예시]
{_FEW_SHOT_EXAMPLES}

[응답 형식]
{{
  "sql": "실행할 SELECT 쿼리 (파라미터: :store_id)",
  "description": "쿼리 설명",
  "relevant_tables": ["사용된 테이블명"]
}}

[질문] {query}
JSON으로만 답하세요."""

        raw = self.gemini.call_gemini_text(prompt, system_instruction=_SQL_SYSTEM, response_type="application/json")
        try:
            data = json.loads(raw)
            sql = data.get("sql", "").strip()
            if not sql.upper().startswith("SELECT"): raise ValueError("Not a SELECT query")
            return GeneratedSQL(sql=sql, description=data.get("description", ""), relevant_tables=data.get("relevant_tables", table_hints))
        except Exception as e:
            logger.error(f"SQL Generation failed: {e}, raw={raw}")
            raise

# ---------------------------------------------------------------------------
# QueryExecutor
# ---------------------------------------------------------------------------

class QueryExecutor:
    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or os.getenv("DATABASE_URL")
        self.engine = create_engine(self.db_url) if self.db_url else None

    def run(self, sql: str, store_id: str) -> tuple[list[dict], list[str]]:
        if not self.engine: return [], []
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), {"store_id": store_id})
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchmany(200)]
            return rows, columns
        except Exception as e:
            logger.error(f"SQL Execution error: {e}")
            raise
