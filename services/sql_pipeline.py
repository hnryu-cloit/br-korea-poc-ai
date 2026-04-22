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


def get_schema_context(table_names: list[str] | None = None) -> str:
    targets = table_names or list(_SCHEMA.keys())
    lines: list[str] = []
    for table_name in targets:
        meta = _SCHEMA.get(table_name)
        if not meta:
            continue
        lines.append(f"### {table_name}")
        lines.append(f"description: {meta['description']}")
        lines.append("columns:")
        for column_name, description in meta["columns"].items():
            lines.append(f"  - {column_name}: {description}")
        lines.append("")
    return "\n".join(lines)


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
    ) -> GeneratedSQL:
        del store_id
        table_hints = table_hints_override or get_table_hints(query_type)
        schema_context = get_schema_context(table_hints)
        today_ref = self._resolve_reference_date()
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
    def _resolve_reference_date() -> str:
        raw = (os.getenv("SQL_REFERENCE_DATE") or "").strip()
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
        if not self.engine:
            raise RuntimeError("Database engine is not initialized.")
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), {"store_id": store_id})
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchmany(200)]
            if agent_name:
                query_logger.log_query(
                    agent_name=agent_name,
                    tables=target_tables or [],
                    query=sql,
                    params=params or {"store_id": store_id},
                )
            return rows, columns
        except Exception as exc:
            logger.error("SQL Execution error: %s", exc)
            raise
