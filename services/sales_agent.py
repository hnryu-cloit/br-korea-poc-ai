from __future__ import annotations
from typing import Dict, List, Any, Optional
from common.logger import init_logger
import os
import re
import json
from sqlalchemy import create_engine, text
from common.query_logger import query_logger

logger = init_logger(__name__)

class SalesAnalysisAgent:
    """
    [Sales Analysis Agent]
    - 시맨틱 레이어(LLM)가 분석한 의도를 바탕으로 동적인 SQL을 실행합니다.
    - DB 조회 히스토리(테이블, 쿼리)를 QueryLogger에 기록합니다.
    """
    def __init__(self, db_url: Optional[str] = None):
        default_db_url = "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc"
        self.db_url = os.getenv("DATABASE_URL", default_db_url)
        self.agent_name = "SalesAnalyzerAgent"

        try:
            self.engine = create_engine(self.db_url)
            logger.info("SalesAnalysisAgent DB 연결 성공.")
        except Exception as e:
            logger.error(f"SalesAnalysisAgent DB 연결 실패: {e}")
            self.engine = None
            
        # 테이블 스키마 정의 (시맨틱 레이어용 컨텍스트) - 표준화된 뷰(View) 중심
        self.schema_definitions = {
            "DAILY_STOR_ITEM": {
                "description": "일별 매장 상품별 매출 집계. 매출 분석 시 최우선 사용.",
                "columns": ["SALE_DT(매출일자, BIGINT YYYYMMDD)", "MASKED_STOR_CD(매장코드)", "ITEM_NM(상품명)", "SALE_QTY(판매수량, NUMERIC)", "SALE_AMT(매출금액, NUMERIC)"]
            },
            "DAILY_STOR_PAY_WAY": {
                "description": "일별 매장 결제수단(채널)별 매출. 배달/오프라인 구분 시 사용.",
                "columns": ["SALE_DT(매출일자, TEXT)", "MASKED_STOR_CD(매장코드)", "PAY_WAY_CD(결제수단코드)", "PAY_AMT(결제금액, NUMERIC)"]
            },
            "PAY_CD": {
                "description": "결제수단 상세 마스터",
                "columns": ["PAY_DC_CD(결제상세코드)", "PAY_DC_NM(결제수단명)"]
            },
            "DAILY_STOR_ITEM_TMZON": {
                "description": "시간대별 매장 상품 매출 (피크 시간 분석용).",
                "columns": ["SALE_DT(매출일자, BIGINT)", "MASKED_STOR_CD(매장코드)", "ITEM_NM(상품명)", "TMZON_DIV(시간대 00~23)", "SALE_QTY(판매수량, NUMERIC)"]
            }
        }

    def get_schema_context(self) -> str:
        """LLM 프롬프트에 주입할 데이터베이스 스키마 정보 반환"""
        return json.dumps(self.schema_definitions, ensure_ascii=False, indent=2)

    def execute_dynamic_sql(self, store_id: str, sql_query: str, target_tables: List[str]) -> List[Dict[str, Any]]:
        """LLM 생성 동적 SQL 실행 및 히스토리 저장 (SELECT 전용)"""
        if not self.engine:
            return [{"error": "DB 엔진이 초기화되지 않았습니다."}]

        # 쌍따옴표 제거 — PostgreSQL 소문자 처리 활용
        safe_sql = sql_query.replace('"', '')

        # CAST(TO_CHAR(...YYYYMMDD...) AS BIGINT) → TO_CHAR(...) 정규화
        safe_sql = re.sub(
            r"CAST\((TO_CHAR\([^)]+,\s*'YYYYMMDD'\))\s+AS\s+(?:BIGINT|INTEGER|INT)\)",
            r"\1",
            safe_sql,
            flags=re.IGNORECASE,
        )
            
        if not safe_sql.strip().upper().startswith("SELECT") and not safe_sql.strip().upper().startswith("WITH"):
            logger.warning("SELECT 쿼리가 아닌 요청 거부")
            return [{"error": "Only SELECT queries are allowed."}]

        try:
            with self.engine.connect() as conn:
                # 쿼리 실행
                result = conn.execute(text(safe_sql), {"store_id": store_id})
                columns = result.keys()
                rows = result.fetchall()
                data = [dict(zip(columns, row)) for row in rows]
                
                # [정합성 검증] 어떤 테이블과 쿼리를 조회했는지 히스토리 로깅
                query_logger.log_query(
                    agent_name=self.agent_name,
                    tables=target_tables,
                    query=safe_sql,
                    params={"store_id": store_id}
                )
                return data
                
        except Exception as e:
            logger.error(f"동적 SQL 실행 오류: {e}\nQuery: {safe_sql}")
            return [{"error": str(e)}]

    def analyze_real_channel_mix(self, store_id: str) -> Dict[str, Any]:
        """채널별(배달 vs 오프라인) 매출 비중 분석"""
        sql = """
            SELECT
                CASE
                    WHEN m.pay_dc_nm LIKE '%%배달%%' OR m.pay_dc_nm LIKE '%%주문%%' OR m.pay_dc_nm LIKE '%%픽업%%' OR m.pay_dc_nm IN ('요기요', '배달의민족', '쿠팡이츠', '해피오더', '땡겨요', '위메프오') THEN 'Delivery'
                    ELSE 'Offline'
                END as channel,
                SUM(CAST(COALESCE(NULLIF(p.pay_amt, ''), '0') AS NUMERIC)) as total_amt
            FROM raw_daily_store_pay_way p
            LEFT JOIN raw_pay_cd m ON p.pay_dtl_cd = m.pay_dc_cd
            WHERE p.masked_stor_cd = :store_id
            GROUP BY 1
        """

        clean_sql = sql

        data = []
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(clean_sql), {"store_id": store_id})
                columns = result.keys()
                rows = result.fetchall()
                data = [dict(zip(columns, row)) for row in rows]

                query_logger.log_query(
                    agent_name=self.agent_name,
                    tables=["raw_daily_store_pay_way", "raw_pay_cd"],
                    query=clean_sql.strip(),
                    params={"store_id": store_id}
                )

        except Exception as e:
            logger.error(f"Channel Mix SQL 실행 오류: {e}")
            return {"error": str(e)}

        valid = [row for row in data if "error" not in row and "channel" in row]

        delivery_amt = sum(row['total_amt'] for row in valid if row['channel'] == 'Delivery')
        total_amt = sum(row['total_amt'] for row in valid)

        delivery_rate = float(round((delivery_amt / total_amt * 100), 1)) if total_amt > 0 else 0.0

        # DELIVERY_EXPANSION_THRESHOLD 환경변수로 배달 확장 기준 설정 (기본값 20%)
        try:
            delivery_threshold = float(os.getenv("DELIVERY_EXPANSION_THRESHOLD", "20"))
        except (ValueError, TypeError):
            delivery_threshold = 20.0

        return {
            "delivery_rate": delivery_rate,
            "online_amt": float(delivery_amt),
            "offline_amt": float(total_amt - delivery_amt),
            "trend": "배달 비중 유지" if delivery_rate > delivery_threshold else "배달 확장 필요"
        }
    def simulate_real_profitability(self, store_id: str) -> Dict[str, Any]:
        """실제 매출 기반 수익성 추정"""
        sql = """
            SELECT SUM(CAST(sale_amt AS NUMERIC)) as total_sales
            FROM raw_daily_store_item
            WHERE masked_stor_cd = :store_id
        """
        data = self.execute_dynamic_sql(store_id, sql, ["raw_daily_store_item"])
        first = data[0] if data and "error" not in data[0] else {}
        total_sales = float(first.get('total_sales') or 0)

        # STANDARD_MARGIN_RATE 환경변수 우선 적용, 미설정 시 0.65 기본값
        _default_margin = 0.65
        try:
            margin_rate = float(os.getenv("STANDARD_MARGIN_RATE", str(_default_margin)))
        except (ValueError, TypeError):
            margin_rate = _default_margin

        estimated_profit = total_sales * margin_rate

        return {
            "total_sales": total_sales,
            "estimated_margin_rate": margin_rate,
            "estimated_profit": estimated_profit,
            "status": "healthy" if margin_rate >= 0.6 else "monitoring"
        }

    def analyze_payment_methods(self, store_id: str) -> List[Dict[str, Any]]:
        """결제 수단별 매출 비중 분석"""
        sql = """
            SELECT
                m.pay_dc_nm as method,
                SUM(CAST(COALESCE(NULLIF(p.pay_amt, ''), '0') AS NUMERIC)) as amount
            FROM raw_daily_store_pay_way p
            LEFT JOIN raw_pay_cd m ON p.pay_dtl_cd = m.pay_dc_cd
            WHERE p.masked_stor_cd = :store_id
            GROUP BY 1
            ORDER BY amount DESC
        """

        clean_sql = sql

        data = []
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(clean_sql), {"store_id": store_id})
                columns = result.keys()
                rows = result.fetchall()
                data = [dict(zip(columns, row)) for row in rows]

                query_logger.log_query(
                    agent_name=self.agent_name,
                    tables=["raw_daily_store_pay_way", "raw_pay_cd"],
                    query=clean_sql.strip(),
                    params={"store_id": store_id}
                )

        except Exception as e:
            logger.error(f"Payment Method SQL 실행 오류: {e}")
            return [{"error": str(e)}]

        return [
            {**row, "amount": float(row["amount"]) if row.get("amount") is not None else 0.0}
            for row in data if "error" not in row
        ]

    def extract_store_profile(self, store_id: str) -> Dict[str, Any]:
        """매장 피크 타임 및 인기 메뉴 추출"""
        # 인기 메뉴
        item_sql = """
            SELECT item_nm, SUM(CAST(sale_qty AS NUMERIC)) as qty
            FROM raw_daily_store_item
            WHERE masked_stor_cd = :store_id
            GROUP BY 1 ORDER BY qty DESC LIMIT 5
        """
        items = self.execute_dynamic_sql(store_id, item_sql, ["raw_daily_store_item"])
        
        # 피크 타임 (DAILY_STOR_ITEM_TMZON 테이블 활용)
        peak_sql = """
            SELECT tmzon_div, SUM(CAST(sale_amt AS NUMERIC)) as amt
            FROM raw_daily_store_item_tmzon
            WHERE masked_stor_cd = :store_id
            GROUP BY 1 ORDER BY amt DESC LIMIT 1
        """
        peak_data = self.execute_dynamic_sql(store_id, peak_sql, ["raw_daily_store_item_tmzon"])
        
        peak_hour = "데이터 없음"
        if peak_data and 'tmzon_div' in peak_data[0]:
            tz = str(peak_data[0]['tmzon_div']).zfill(2)
            next_tz = str(int(tz) + 1).zfill(2)
            peak_hour = f"{tz}:00~{next_tz}:00"
        
        return {
            "top_items": [row['item_nm'] for row in items] if items else [],
            "peak_hour": peak_hour
        }

    def calculate_comparison_metrics(self, store_id: str) -> Dict[str, Any]:
        """전주 대비 매출 비교 등 성장 지표 계산"""
        # 현재 데이터베이스에 적재된 최신 날짜를 기준으로 과거 7일과 그 이전 7일을 비교
        sql = """
            SELECT
                SUM(CASE WHEN sale_dt >= TO_CHAR(CURRENT_DATE - INTERVAL '7 days', 'YYYYMMDD') THEN CAST(sale_amt AS NUMERIC) ELSE 0 END) as recent_7d,
                SUM(CASE WHEN sale_dt < TO_CHAR(CURRENT_DATE - INTERVAL '7 days', 'YYYYMMDD') AND sale_dt >= TO_CHAR(CURRENT_DATE - INTERVAL '14 days', 'YYYYMMDD') THEN CAST(sale_amt AS NUMERIC) ELSE 0 END) as prev_7d
            FROM raw_daily_store_item
            WHERE masked_stor_cd = :store_id
        """
        data = self.execute_dynamic_sql(store_id, sql, ["raw_daily_store_item"])
        first = data[0] if data and "error" not in data[0] else {}
        recent = float(first.get('recent_7d') or 0)
        prev = float(first.get('prev_7d') or 0)
        
        growth = ((recent - prev) / prev * 100) if prev > 0 else 0
        
        return {
            "recent_4w_sales": recent, # 7일로 계산했으나 필드명은 규약에 맞춤
            "previous_4w_sales": prev,
            "growth_rate": round(growth, 1)
        }

    def analyze_cross_sell(self, store_id: str) -> List[Dict[str, Any]]:
        """일별 동반 판매 상품 조합 분석 (raw_daily_store_item 기반 일자 수준 공출현)"""
        # 영수증 단위 데이터(ORD_DTL)가 없으므로 raw_daily_store_item의 일자 수준 공출현으로 대체
        sql = """
            WITH daily_items AS (
                SELECT
                    CAST(sale_dt AS TEXT) AS sale_dt,
                    COALESCE(NULLIF(TRIM(CAST(item_nm AS TEXT)), ''), CAST(item_cd AS TEXT)) AS item_nm
                FROM raw_daily_store_item
                WHERE CAST(masked_stor_cd AS TEXT) = :store_id
                  AND NULLIF(TRIM(CAST(item_nm AS TEXT)), '') IS NOT NULL
                  AND CAST(sale_dt AS TEXT) >= TO_CHAR(CURRENT_DATE - INTERVAL '28 days', 'YYYYMMDD')
            ),
            item_pairs AS (
                SELECT t1.item_nm AS item_a, t2.item_nm AS item_b, COUNT(*) AS pair_count
                FROM daily_items t1
                JOIN daily_items t2 ON t1.sale_dt = t2.sale_dt
                WHERE t1.item_nm < t2.item_nm
                  AND NOT ((t1.item_nm LIKE '%%먼치킨%%' OR t1.item_nm LIKE '%%미니%%') AND
                           (t2.item_nm LIKE '%%먼치킨%%' OR t2.item_nm LIKE '%%미니%%'))
                GROUP BY 1, 2
            ),
            item_counts AS (
                SELECT item_nm, COUNT(DISTINCT sale_dt) AS item_count
                FROM daily_items
                GROUP BY 1
            ),
            total_tx AS (
                SELECT COUNT(DISTINCT sale_dt) AS total_count FROM daily_items
            )
            SELECT
                p.item_a, p.item_b, p.pair_count,
                ROUND(CAST(p.pair_count AS NUMERIC) / NULLIF(t.total_count, 0), 4) AS support,
                ROUND(CAST(p.pair_count AS NUMERIC) / NULLIF(c1.item_count, 0), 4) AS confidence,
                ROUND(
                    (CAST(p.pair_count AS NUMERIC) / NULLIF(t.total_count, 0)) /
                    NULLIF(
                        (CAST(c1.item_count AS NUMERIC) / NULLIF(t.total_count, 0)) *
                        (CAST(c2.item_count AS NUMERIC) / NULLIF(t.total_count, 0)),
                    0), 2
                ) AS lift
            FROM item_pairs p
            CROSS JOIN total_tx t
            JOIN item_counts c1 ON p.item_a = c1.item_nm
            JOIN item_counts c2 ON p.item_b = c2.item_nm
            WHERE t.total_count > 0
            ORDER BY lift DESC, support DESC
            LIMIT 10
        """
        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(sql), {"store_id": store_id})
                columns = result.keys()
                rows = result.fetchall()
                data = [dict(zip(columns, row)) for row in rows]

                query_logger.log_query(
                    agent_name=self.agent_name,
                    tables=["raw_daily_store_item"],
                    query=sql.strip(),
                    params={"store_id": store_id}
                )

                return data
        except Exception as e:
            logger.error(f"Cross sell SQL 실행 오류: {e}")
            return [{"error": str(e)}]

    def get_data_lineage(self) -> List[Dict[str, Any]]:
        """해당 에이전트가 실행한 쿼리 히스토리 반환 및 초기화(세션 격리)"""
        lineage = query_logger.get_history(self.agent_name)
        query_logger.clear_history() # 가져온 후 현재 세션 비우기
        return lineage
