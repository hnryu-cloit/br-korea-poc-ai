from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

from common.gemini import Gemini
from common.query_logger import query_logger

logger = logging.getLogger(__name__)

_DEFAULT_DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc"
_MAX_FETCH_ROWS = 300

_SCHEMA: dict[str, dict[str, object]] = {
    "core_daily_item_sales": {
        "description": "Typed daily item sales view for store-level sales analysis.",
        "columns": {
            "masked_stor_cd": "store id",
            "sale_dt": "business date in YYYYMMDD text",
            "item_nm": "item name",
            "sale_qty": "daily sold quantity as numeric",
            "sale_amt": "daily sales amount as numeric",
            "actual_sale_amt": "actual sales amount as numeric",
            "net_sale_amt": "net sales amount as numeric",
        },
    },
    "raw_production_extract": {
        "description": "Raw production data by store, date, and item.",
        "columns": {
            "masked_stor_cd": "store id",
            "prod_dt": "production date in YYYYMMDD text",
            "item_nm": "item name",
            "prod_qty": "first production quantity as text number",
            "prod_qty_2": "second production quantity as text number",
            "prod_qty_3": "third production quantity as text number",
            "reprod_qty": "reproduction quantity as text number",
        },
    },
    "raw_order_extract": {
        "description": "Raw order detail data by delivery date and item.",
        "columns": {
            "masked_stor_cd": "store id",
            "dlv_dt": "delivery date in YYYYMMDD text",
            "item_nm": "item name",
            "ord_qty": "ordered quantity as text number",
            "ord_amt": "ordered amount as text number",
            "confrm_qty": "confirmed quantity as text number",
            "confrm_amt": "confirmed amount as text number",
        },
    },
    "raw_inventory_extract": {
        "description": "Raw inventory extract by stock date and item.",
        "columns": {
            "masked_stor_cd": "store id",
            "stock_dt": "stock date in YYYYMMDD text",
            "item_nm": "item name",
            "stock_qty": "stock quantity as text number",
            "sale_qty": "sale quantity as text number",
            "prod_in_qty": "production in quantity as text number",
            "prod_out_qty": "production out quantity as text number",
        },
    },
    "raw_daily_store_pay_way": {
        "description": "Raw payment breakdown by store and sale date.",
        "columns": {
            "masked_stor_cd": "store id",
            "sale_dt": "business date in YYYYMMDD text",
            "pay_way_cd": "payment method code",
            "pay_dtl_cd": "payment detail code",
            "pay_way_cd_nm": "payment method name from source",
            "pay_dtl_cd_nm": "payment detail name from source",
            "pay_amt": "payment amount as text number",
        },
    },
    "raw_pay_cd": {
        "description": "Payment code master.",
        "columns": {
            "pay_dc_cd": "payment detail code",
            "pay_dc_nm": "payment detail name",
            "pay_dc_grp_type": "payment group type",
            "pay_dc_type": "payment type",
        },
    },
    "core_channel_sales": {
        "description": "Typed online or channel sales view by date and channel.",
        "columns": {
            "masked_stor_cd": "store id",
            "sale_dt": "business date in YYYYMMDD text",
            "ho_chnl_nm": "channel name",
            "ho_chnl_div": "channel division",
            "sale_amt": "sales amount as numeric",
            "ord_cnt": "order count as numeric",
        },
    },
}

_TABLE_HINTS: dict[str, list[str]] = {
    "sales": ["core_daily_item_sales"],
    "production": ["raw_production_extract", "core_daily_item_sales"],
    "order": ["raw_order_extract"],
    "channel": ["raw_daily_store_pay_way", "raw_pay_cd", "core_channel_sales"],
    "inventory": ["raw_inventory_extract", "raw_production_extract", "core_daily_item_sales"],
    "general": ["core_daily_item_sales", "raw_production_extract", "raw_order_extract"],
}

_SQL_SYSTEM = """\
You generate PostgreSQL SELECT queries for the bakery franchise analytics service.

Rules:
1. Only return JSON.
2. Only generate SELECT queries.
3. Always filter by "masked_stor_cd" = :store_id when the table contains that column.
4. For rolling periods such as recent N days, exclude the reference date itself and end at the previous day.
5. Use the explicit reference date provided in the prompt. Do not use CURRENT_DATE.
6. Prefer only the tables listed in the prompt.
7. When numeric source columns are stored as text, cast safely with NULLIF(column, '')::numeric.
"""


# DB information_schema 기반 동적 스키마 캐시 (1회 로드)
_DB_SCHEMA_CACHE: dict[str, list[tuple[str, str]]] | None = None


def _load_db_schema_from_information_schema() -> dict[str, list[tuple[str, str]]]:
    """information_schema.columns 에서 (테이블 → [(컬럼명, 데이터타입), ...])를 추출한다."""
    global _DB_SCHEMA_CACHE
    if _DB_SCHEMA_CACHE is not None:
        return _DB_SCHEMA_CACHE

    db_url = os.getenv("DATABASE_URL", _DEFAULT_DB_URL)
    schema_map: dict[str, list[tuple[str, str]]] = {}
    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                    """
                )
            ).all()
        for table_name, column_name, data_type in rows:
            schema_map.setdefault(str(table_name), []).append((str(column_name), str(data_type)))
    except Exception as exc:
        logger.warning("DB 스키마 로드 실패(인메모리 _SCHEMA만 사용): %s", exc)

    _DB_SCHEMA_CACHE = schema_map
    return schema_map


def list_known_tables() -> list[str]:
    """인메모리 + DB 스키마에서 알려진 모든 테이블명을 반환한다."""
    db_schema = _load_db_schema_from_information_schema()
    names = set(_SCHEMA.keys()) | set(db_schema.keys())
    return sorted(names)


# 현재 질의에 필요한 스키마 스니펫만 렌더링 (인메모리 + DB 동적 스키마 결합)
def get_schema_context(table_names: list[str] | None = None) -> str:
    db_schema = _load_db_schema_from_information_schema()
    targets = table_names or sorted(set(_SCHEMA.keys()) | set(db_schema.keys()))

    lines: list[str] = []
    for table_name in targets:
        meta = _SCHEMA.get(table_name)
        db_columns = db_schema.get(table_name, [])
        if not meta and not db_columns:
            continue

        lines.append(f"### {table_name}")
        if meta:
            lines.append(f"description: {meta['description']}")
        lines.append("columns:")

        seen: set[str] = set()
        if meta:
            for column_name, description in meta["columns"].items():
                lines.append(f"  - {column_name}: {description}")
                seen.add(column_name)
        for column_name, data_type in db_columns:
            if column_name in seen:
                continue
            lines.append(f"  - {column_name} ({data_type})")
        lines.append("")

    # hint 된 테이블 외에도 후보가 있으면 카탈로그 형태로 이름만 추가 노출
    if table_names:
        catalog = sorted(t for t in (set(_SCHEMA.keys()) | set(db_schema.keys())) if t not in set(table_names))
        if catalog:
            lines.append("### (other tables — name only)")
            for chunk_start in range(0, len(catalog), 8):
                lines.append("  - " + ", ".join(catalog[chunk_start:chunk_start + 8]))

    return "\n".join(lines)


# 에이전트/도메인 질의 유형별 기본 테이블 목록 반환
def get_table_hints(query_type: str) -> list[str]:
    return _TABLE_HINTS.get(query_type, _TABLE_HINTS["general"])


@dataclass
class GeneratedSQL:
    sql: str
    description: str
    relevant_tables: list[str]
    queried_period: dict[str, str]


class SQLGenerator:
    def __init__(self, gemini: Gemini) -> None:
        self.gemini = gemini

    def generate(
        self,
        query: str,
        store_id: str,
        query_type: str = "general",
        table_hints_override: list[str] | None = None,
        intent_summary: str | None = None,
        reference_date: str | None = None,
    ) -> GeneratedSQL:
        # SQL 생성 프롬프트 구성 및 모델 출력 정규화
        del store_id
        table_hints = table_hints_override or get_table_hints(query_type)
        schema_context = get_schema_context(table_hints)
        today_ref = self._resolve_reference_date(reference_date)
        period_hint = self._infer_period(query, query_type, today_ref)
        few_shot_examples = self._build_examples(today_ref)

        prompt = f"""[Reference Date]
{today_ref}

[Intent]
{intent_summary or query_type}

[Available Tables]
{schema_context}

[Date Guide]
- Treat {today_ref} as today.
- For "recent N days", exclude {today_ref} and end at the previous day.

[Required Period]
- mode: {period_hint["mode"]}
- from: {period_hint["from"]}
- to: {period_hint["to"]}
- label: {period_hint["label"]}
- Respect this date window unless the question explicitly requires a narrower one.

[Examples]
{few_shot_examples}

[Response JSON]
{{
  "sql": "SELECT ...",
  "description": "short description",
  "relevant_tables": ["table_a", "table_b"]
}}

[Question]
{query}
"""

        raw = self.gemini.call_gemini_text(
            prompt,
            system_instruction=_SQL_SYSTEM,
            response_type="application/json",
        )
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            sql = str(data.get("sql", "")).strip()
            if not sql.upper().startswith("SELECT"):
                raise ValueError("Not a SELECT query")
            relevant_tables = data.get("relevant_tables", table_hints)
            return GeneratedSQL(
                sql=sql,
                description=str(data.get("description", "")),
                relevant_tables=[str(item) for item in relevant_tables],
                queried_period=period_hint,
            )
        except Exception as exc:
            logger.error("SQL Generation failed: %s, raw=%s", exc, raw)
            raise

    @staticmethod
    def _resolve_reference_date(reference_date: str | None = None) -> str:
        # 상대적 날짜 질의에 사용되는 기준일(오늘) 결정
        raw = (reference_date or os.getenv("SQL_REFERENCE_DATE") or "").strip()
        if raw:
            for fmt in ("%Y-%m-%d", "%Y%m%d"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            logger.warning("Invalid SQL_REFERENCE_DATE=%s. Falling back to system date.", raw)
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _build_examples(reference_date: str) -> str:
        # 날짜 안전 SQL 예시를 포함한 few-shot 프롬프트 구성
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        yesterday = (ref - timedelta(days=1)).strftime("%Y%m%d")
        recent3_start = (ref - timedelta(days=3)).strftime("%Y%m%d")
        return (
            "Question: 최근 3일 평균 생산량 알려줘\n"
            "SQL: SELECT AVG("
            "COALESCE(NULLIF(prod_qty, '')::numeric, 0) + "
            "COALESCE(NULLIF(prod_qty_2, '')::numeric, 0) + "
            "COALESCE(NULLIF(prod_qty_3, '')::numeric, 0)"
            f") AS avg_prod FROM raw_production_extract WHERE masked_stor_cd = :store_id AND prod_dt BETWEEN '{recent3_start}' AND '{yesterday}';\n\n"
            "Question: 결제수단별 매출 알려줘\n"
            "SQL: SELECT "
            "COALESCE(NULLIF(p.pay_dtl_cd_nm, ''), c.pay_dc_nm, p.pay_way_cd_nm, 'UNKNOWN') AS payment_method, "
            "SUM(COALESCE(NULLIF(p.pay_amt, '')::numeric, 0)) AS total_sales_amount "
            "FROM raw_daily_store_pay_way p "
            "LEFT JOIN raw_pay_cd c ON p.pay_dtl_cd = c.pay_dc_cd "
            "WHERE p.masked_stor_cd = :store_id "
            f"AND p.sale_dt <= '{yesterday}' "
            "GROUP BY COALESCE(NULLIF(p.pay_dtl_cd_nm, ''), c.pay_dc_nm, p.pay_way_cd_nm, 'UNKNOWN') "
            "ORDER BY total_sales_amount DESC;"
        )

    @staticmethod
    def _infer_period(query: str, query_type: str, reference_date: str) -> dict[str, str]:
        # 상대적 날짜 표현을 명시적 조회 기간으로 변환
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
        yesterday = ref - timedelta(days=1)
        normalized = query.replace(" ", "")

        def fmt(day: datetime) -> str:
            return day.strftime("%Y-%m-%d")

        if "어제" in query:
            return {
                "mode": "single_day",
                "from": fmt(yesterday),
                "to": fmt(yesterday),
                "label": f"{fmt(yesterday)} 하루",
            }

        recent_match = None
        for pattern in (r"최근(\d+)일", r"(\d+)일간", r"(\d+)일동안"):
            match = re.search(pattern, normalized)
            if match:
                recent_match = int(match.group(1))
                break
        if recent_match:
            start = ref - timedelta(days=recent_match)
            return {
                "mode": f"rolling_{recent_match}_days",
                "from": fmt(start),
                "to": fmt(yesterday),
                "label": f"{fmt(start)} ~ {fmt(yesterday)}",
            }

        if any(token in query for token in ("최근 일주일", "지난 일주일", "최근 1주", "최근 일주간")):
            start = ref - timedelta(days=7)
            return {
                "mode": "rolling_7_days",
                "from": fmt(start),
                "to": fmt(yesterday),
                "label": f"{fmt(start)} ~ {fmt(yesterday)}",
            }

        if any(token in query for token in ("최근 한달", "최근 한 달", "지난 한달", "지난 한 달")):
            start = ref - timedelta(days=30)
            return {
                "mode": "rolling_30_days",
                "from": fmt(start),
                "to": fmt(yesterday),
                "label": f"{fmt(start)} ~ {fmt(yesterday)}",
            }

        pattern_tokens = (
            "추이",
            "패턴",
            "비중",
            "추세",
            "메뉴별",
            "상품별",
            "결제수단별",
            "채널별",
        )
        if query_type in {"sales", "channel"} or any(token in query for token in pattern_tokens):
            start = ref - timedelta(days=30)
            return {
                "mode": "rolling_30_days",
                "from": fmt(start),
                "to": fmt(yesterday),
                "label": f"{fmt(start)} ~ {fmt(yesterday)}",
            }

        return {
            "mode": "up_to_yesterday",
            "from": fmt(yesterday),
            "to": fmt(yesterday),
            "label": f"{fmt(yesterday)} 하루",
        }


class QueryExecutor:
    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or os.getenv("DATABASE_URL") or _DEFAULT_DB_URL
        self.engine = create_engine(self.db_url) if self.db_url else None

    def run(
        self,
        sql: str,
        store_id: str,
        agent_name: str | None = None,
        target_tables: list[str] | None = None,
        params: dict[str, object] | None = None,
    ) -> tuple[list[dict], list[str]]:
        # Executes the generated read-only SQL and records trace metadata.
        if not self.engine:
            raise RuntimeError("Database engine is not initialized.")
        bound_params: dict[str, object] = {"store_id": store_id}
        if params:
            bound_params.update(params)
        bound_params["store_id"] = store_id
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), bound_params)
                columns = list(result.keys())
                fetched_rows = result.fetchmany(_MAX_FETCH_ROWS)
                rows = [dict(zip(columns, row)) for row in fetched_rows]
            if agent_name:
                query_logger.log_query(
                    agent_name=agent_name,
                    tables=target_tables or [],
                    query=sql,
                    params=bound_params,
                )
            return rows, columns
        except Exception as exc:
            logger.error("SQL Execution error: %s", exc)
            raise
