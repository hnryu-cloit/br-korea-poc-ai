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
# SchemaRegistry
# ---------------------------------------------------------------------------

_SCHEMA: dict = {
    "raw_daily_store_item": {
        "description": "일별 매장-상품 매출 집계 (가장 기본적인 매출 원천)",
        "columns": {
            "masked_stor_cd": "매장 코드 (필터 기준)",
            "sale_dt": "판매일 (YYYYMMDD 문자열, 날짜 비교: sale_dt >= '20260101' 형식 사용)",
            "item_nm": "상품명",
            "sale_amt": "매출액 원, CAST(sale_amt AS NUMERIC) 필요",
            "sale_qty": "판매 수량, CAST(sale_qty AS NUMERIC) 필요",
            "dc_amt": "할인 금액 원",
        },
        "notes": "피크 시간대·상품별 매출·기간 비교 등 대부분의 매출 질문에 사용",
    },
    "raw_daily_store_pay_way": {
        "description": "일별 매장 결제수단별 매출 (배달·온라인·오프라인 채널 분류)",
        "columns": {
            "masked_stor_cd": "매장 코드",
            "sale_dt": "판매일 YYYYMMDD",
            "pay_way_cd": "결제수단 코드 ('01'=신용카드, '02'=현금, '07'~'11'=간편결제/배달)",
            "pay_amt": "결제 금액 원, CAST(pay_amt AS NUMERIC) 필요",
            "pay_dtl_cd": "결제 상세 코드 (raw_pay_cd JOIN 키)",
        },
        "notes": "배달앱 매출 분리 시 raw_pay_cd LEFT JOIN: ON pay_dtl_cd = pay_dc_cd",
    },
    "raw_pay_cd": {
        "description": "결제수단 코드 참조 테이블",
        "columns": {
            "pay_dc_cd": "결제 상세 코드",
            "pay_dc_nm": "결제수단명 (예: '요기요', '배달의민족', '해피오더')",
        },
    },
    "raw_daily_store_cpi_tmzon": {
        "description": "캠페인(T데이 등) 시간대별 매출 집계 — SALE_DT 컬럼 없음, 날짜 필터 불가",
        "columns": {
            "masked_stor_cd": "매장 코드",
            "cpi_cd": "캠페인 코드",
            "cpi_nm": "캠페인명",
            "act_amt_00~act_amt_23": "각 시간대(00~23시) 실매출액 (24개 컬럼)",
            "qty_00~qty_23": "각 시간대(00~23시) 판매수량 (24개 컬럼)",
        },
        "notes": "SALE_DT 없음 — 날짜 범위 필터 불가. 캠페인별 시간대 분포 조회에만 사용",
    },
    "raw_production_extract": {
        "description": "생산 이력 — 총 생산량은 prod_qty + prod_qty_2 + prod_qty_3 합산",
        "columns": {
            "masked_stor_cd": "매장 코드",
            "item_cd": "상품 코드",
            "item_nm": "상품명",
            "prod_dt": "생산일 YYYYMMDD",
            "prod_qty": "1차 생산 수량",
            "prod_qty_2": "2차 생산 수량",
            "prod_qty_3": "3차 생산 수량",
        },
        "notes": "총 생산량 = COALESCE(prod_qty,0) + COALESCE(prod_qty_2,0) + COALESCE(prod_qty_3,0)",
    },
    "raw_order_extract": {
        "description": "발주 이력 — 점주 발주 수량(ord_qty)과 실 출하 확정 수량(confrm_qty) 구분",
        "columns": {
            "masked_stor_cd": "매장 코드",
            "item_cd": "상품 코드",
            "item_nm": "상품명",
            "dlv_dt": "배송일 YYYYMMDD",
            "ord_qty": "점주 발주 수량",
            "confrm_qty": "실 출하 확정 수량",
            "ord_rec_qty": "시스템 권고 발주 수량",
        },
    },
    "raw_inventory_extract": {
        "description": "재고 이력",
        "columns": {
            "masked_stor_cd": "매장 코드",
            "item_cd": "상품 코드",
            "stock_dt": "재고 기준일 YYYYMMDD",
            "stock_qty": "재고 수량",
        },
    },
}

_TABLE_HINTS: dict[str, list[str]] = {
    "sales": ["raw_daily_store_item"],
    "channel": ["raw_daily_store_pay_way", "raw_pay_cd"],
    "campaign": ["raw_daily_store_cpi_tmzon", "raw_daily_store_item"],
    "cross_sell": ["raw_daily_store_item"],
    "production": ["raw_production_extract"],
    "order": ["raw_order_extract"],
    "inventory": ["raw_inventory_extract"],
    "general": ["raw_daily_store_item", "raw_daily_store_pay_way"],
}


def get_schema_context(table_names: list[str] | None = None) -> str:
    """지정한 테이블의 스키마 설명 문자열 생성 (LLM 프롬프트 삽입용)"""
    targets = table_names or list(_SCHEMA.keys())
    lines: list[str] = []
    for tbl in targets:
        if tbl not in _SCHEMA:
            continue
        meta = _SCHEMA[tbl]
        lines.append(f"### {tbl}")
        lines.append(f"설명: {meta['description']}")
        lines.append("컬럼:")
        for col, desc in meta["columns"].items():
            lines.append(f"  - {col}: {desc}")
        if "notes" in meta:
            lines.append(f"주의: {meta['notes']}")
        lines.append("")
    return "\n".join(lines)


def get_table_hints(query_type: str) -> list[str]:
    """질의 유형에 맞는 우선 조회 테이블 목록 반환"""
    return _TABLE_HINTS.get(query_type, _TABLE_HINTS["general"])


# ---------------------------------------------------------------------------
# SQLGenerator
# ---------------------------------------------------------------------------

_SQL_SYSTEM = """\
당신은 한국 베이커리 프랜차이즈 분석 시스템의 PostgreSQL 전문가입니다.

[필수 규칙]
1. SELECT 문만 생성한다. 변경 쿼리(INSERT/UPDATE/DELETE/DROP 등) 절대 금지.
2. 항상 masked_stor_cd = :store_id 필터를 포함한다 (컬럼명 소문자, 쌍따옴표 금지).
3. 날짜 비교: sale_dt >= '20260101' 형식 사용 (문자열 리터럴, 따옴표 필수).
4. 기본 기간: MAX(sale_dt) 기준 최근 28일.
5. 숫자 컬럼은 CAST(컬럼명 AS NUMERIC) 처리한다.
6. LIMIT 200 이하로 결과를 제한한다.
7. 테이블명·컬럼명은 모두 소문자, 쌍따옴표 사용 금지.
8. 응답은 JSON만 반환한다 (설명 텍스트 없이).
"""


@dataclass
class GeneratedSQL:
    sql: str
    description: str
    relevant_tables: list[str]


class SQLGenerator:
    """사용자 질의 + 스키마 컨텍스트를 LLM에 넘겨 SELECT SQL을 추론한다."""

    def __init__(self, gemini: Gemini) -> None:
        self.gemini = gemini

    def generate(self, query: str, store_id: str, query_type: str = "general") -> GeneratedSQL:
        """자연어 질의와 스키마 컨텍스트를 LLM에 전달해 SELECT SQL 생성"""
        table_hints = get_table_hints(query_type)
        schema_context = get_schema_context(table_hints)

        prompt = f"""[DB 스키마]
        {schema_context}
        
        [응답 형식]
        {{
          "sql": "실행할 SELECT 쿼리 (파라미터: :store_id)",
          "description": "이 쿼리가 조회하는 내용 한 문장 (점주에게 보여줄 근거 문구)",
          "relevant_tables": ["사용된 테이블명 목록"]
        }}
        
        [점주 질문]
        {query}
        
        JSON만 반환하세요."""

        raw = self.gemini.call_gemini_text(
            prompt,
            system_instruction=_SQL_SYSTEM,
            response_type="application/json",
        )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("SQLGenerator: JSON 파싱 실패, raw=%s", raw[:200])
            raise ValueError("SQL 생성 실패: LLM 응답을 파싱할 수 없습니다.")

        sql = data.get("sql", "").strip()
        if not sql.upper().lstrip().startswith("SELECT"):
            raise ValueError("SQL 안전성 검증 실패: SELECT 문이 아닙니다.")

        logger.info("SQLGenerator: 생성 완료 — %s", data.get("description", ""))
        return GeneratedSQL(
            sql=sql,
            description=data.get("description", ""),
            relevant_tables=data.get("relevant_tables", table_hints),
        )


# ---------------------------------------------------------------------------
# QueryExecutor
# ---------------------------------------------------------------------------

_MAX_ROWS = 200
_FORBIDDEN = {
    "insert",
    "update",
    "delete",
    "drop",
    "truncate",
    "alter",
    "create",
    "grant",
    "revoke",
}


class QueryExecutionError(Exception):
    pass


class QueryExecutor:
    """생성된 SELECT SQL을 안전하게 실행하고 결과를 반환한다."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or os.getenv("DATABASE_URL")
        if not self.db_url:
            logger.error("DATABASE_URL이 설정되지 않아 QueryExecutor DB 연결을 비활성화합니다.")
            self.engine = None
            return
        try:
            self.engine = create_engine(self.db_url)
        except Exception as e:
            logger.error("QueryExecutor DB 연결 실패: %s", e)
            self.engine = None

    def run(self, sql: str, store_id: str) -> tuple[list[dict], list[str]]:
        # 금지 키워드 및 SELECT 여부 사전 검증
        self._validate(sql)
        if not self.engine:
            raise QueryExecutionError("DB 연결이 없습니다.")
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), {"store_id": store_id})
                columns = list(result.keys())
                # 최대 행 수 제한 적용 후 dict 변환
                rows = [dict(zip(columns, row)) for row in result.fetchmany(_MAX_ROWS)]
            logger.info("QueryExecutor: %d행 조회 (store=%s)", len(rows), store_id)
            return rows, columns
        except SQLAlchemyError as e:
            logger.error("QueryExecutor 실행 오류: %s", e)
            raise QueryExecutionError(f"쿼리 실행 실패: {e}") from e

    def run_as_dataframe(self, sql: str, store_id: str) -> pd.DataFrame:
        """SQL 실행 결과를 pandas DataFrame으로 변환해 반환"""
        rows, columns = self.run(sql, store_id)
        return pd.DataFrame(rows, columns=columns)

    @staticmethod
    def _validate(sql: str) -> None:
        normalized = sql.strip().lower()
        if not normalized.startswith("select"):
            raise QueryExecutionError("SELECT 문만 허용됩니다.")
        for kw in _FORBIDDEN:
            if f" {kw} " in f" {normalized} ":
                raise QueryExecutionError(f"금지된 키워드 포함: {kw}")
