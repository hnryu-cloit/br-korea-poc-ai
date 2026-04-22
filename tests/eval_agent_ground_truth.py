"""
Agent Ground-Truth 정합성 평가 스크립트 (L1 + L2)

L1: ground truth SQL을 DB에 직접 실행 → 기댓값(응답) 일치 여부
L2: AgentOrchestrator에 자연어 질문 → 응답 텍스트의 숫자가 DB 실제값과 일치 여부

CSV 입력: docs/agent-ground-truth-questions.csv
CSV 출력: docs/eval_agent_ground_truth_result.csv

사용법 (br-korea-poc-ai 디렉토리에서 실행):
    python tests/eval_agent_ground_truth.py
    python tests/eval_agent_ground_truth.py --store_id POC_001
    python tests/eval_agent_ground_truth.py --limit 5     # 처음 5건만 (빠른 테스트용)
    python tests/eval_agent_ground_truth.py --skip-l2     # L1만 실행 (LLM 호출 없음)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

AI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_ROOT))

load_dotenv(AI_ROOT / ".env")

from sqlalchemy import create_engine, text

CSV_INPUT  = AI_ROOT.parent / "docs" / "agent-ground-truth-questions.csv"
CSV_OUTPUT = AI_ROOT.parent / "docs" / "eval_agent_ground_truth_result.csv"

_DEFAULT_DB_URL = "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc"

_APP_LEVEL_MARKERS = [
    "/* 3 consecutive zero-sales hours rule applied in app */",
]

OUTPUT_FIELDS = [
    "no",
    "L1_DB정합성",    # O / X / APP / ERROR
    "L2_숫자정합성",  # O / X / EMPTY / ERROR / - (미실행)
    "지점코드",
    "조회일자",
    "질문",
    "키워드",
    "조회테이블",
    "기댓값(DB응답)",
    "L1_실제DB값",
    "LLM예상답변",
    "L2_Agent응답텍스트",
    "L2_불일치숫자",
    "처리경로",
    "쿼리유형",
    "비고",           # 오류/APP 사유
    "L2_Agent생성SQL",  # 열람용 (Excel에서 숨김 가능)
    "쿼리문",
]


# ── 값 정규화 ─────────────────────────────────

def _norm_val(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, Decimal):
        val = float(val)
    if isinstance(val, float):
        if val == int(val):
            return str(int(val))
        return str(round(val, 1))
    s = str(val).strip()
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _norm_expected(expected: str) -> str:
    parts = [p.strip() for p in expected.split(" | ")]
    return " | ".join(re.sub(r"(?<=\d),(?=\d)", "", p) for p in parts)


def normalize_db_result(rows: list[dict], sql: str) -> tuple[str, bool]:
    """DB 결과 → (정규화 문자열, is_app_level)"""
    is_app = any(m in sql for m in _APP_LEVEL_MARKERS)
    if is_app:
        return f"[rows={len(rows)}]", True
    if not rows:
        return "", False
    if len(rows) > 1:
        return f"[rows={len(rows)}]", True
    values = [_norm_val(v) for v in rows[0].values()]
    return (" | ".join(values) if len(values) > 1 else values[0]), False


# ── DB 실행 ───────────────────────────────────

def run_sql(sql: str, db_url: str) -> list[dict]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql)).mappings().all()]


# ── L1 평가 ───────────────────────────────────

def evaluate_l1(sql: str, expected: str, db_url: str) -> tuple[str, str]:
    """
    Returns: (status "O"/"X"/"APP"/"ERROR", actual_str)
    """
    try:
        rows = run_sql(sql, db_url)
        actual, is_app = normalize_db_result(rows, sql)
        if is_app:
            return "APP", actual
        expected_norm = _norm_expected(expected)
        return ("O" if actual == expected_norm else "X"), actual
    except Exception as e:
        return "ERROR", str(e)


# ── L2 숫자 정합성 ────────────────────────────

def _extract_numbers(text: str) -> set[float]:
    numbers: set[float] = set()
    for token in re.findall(r"\d+(?:\.\d+)?", str(text)):
        value = float(token)
        if 20000101 <= value <= 20991231:
            continue
        numbers.add(value)
    return numbers


def _numbers_from_rows(rows: list[dict]) -> set[float]:
    numbers: set[float] = set()
    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, Decimal):
                value = float(value)
            if isinstance(value, (int, float)):
                n = float(value)
                numbers.add(n)
                if abs(n - round(n)) < 1e-9:
                    numbers.add(float(int(round(n))))
    return numbers


def _is_close(pool: set[float], target: float, tol: float = 0.05) -> bool:
    return any(abs(n - target) <= tol for n in pool)


def evaluate_l2_numeric(answer_text: str, db_rows: list[dict], question: str) -> tuple[str, str]:
    """
    Returns: (status "O"/"X"/"EMPTY", unexpected_numbers_str)
    """
    if not answer_text.strip():
        return "EMPTY", ""
    answer_numbers = _extract_numbers(answer_text)
    allowed = _extract_numbers(question)
    allowed.update(_numbers_from_rows(db_rows))
    unexpected = sorted(n for n in answer_numbers if not _is_close(allowed, n))
    return ("X", str(unexpected)) if unexpected else ("O", "")


# ── Agent 응답 파싱 ───────────────────────────

def extract_answer_text(response: Any) -> str:
    if isinstance(response, dict):
        if isinstance(response.get("answer"), dict):
            return str(response["answer"].get("text", ""))
        return str(response.get("text", ""))
    return str(response)


def extract_agent_sql(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    for item in response.get("data_lineage", []):
        if isinstance(item, dict) and item.get("agent") == "GroundedWorkflow":
            return str(item.get("query", ""))
    return str(response.get("sql", ""))


# ── 메인 평가 루프 ────────────────────────────

async def run_evaluation(
    store_id_filter: str | None,
    date_filter: str | None,
    limit: int | None,
    skip_l2: bool,
    db_url: str,
) -> None:
    if not CSV_INPUT.exists():
        print(f"CSV not found: {CSV_INPUT}")
        sys.exit(1)

    with open(CSV_INPUT, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if store_id_filter:
        rows = [r for r in rows if r["지점코드(혹은 지점명)"].startswith(store_id_filter)]
    if date_filter:
        rows = [r for r in rows if r["조회 일자(가상)"] == date_filter]
    if limit:
        rows = rows[:limit]

    if not rows:
        print("No matching rows.")
        return

    orchestrator = None
    if not skip_l2:
        try:
            from common.gemini import Gemini
            from services.orchestrator import AgentOrchestrator
            orchestrator = AgentOrchestrator(Gemini())
            print("[L2] AgentOrchestrator initialized.\n")
        except Exception as e:
            print(f"[L2] Orchestrator init failed: {e}  -> L2 skipped.\n")

    total = len(rows)
    l1_pass = l1_fail = l1_app = l1_err = 0
    l2_pass = l2_fail = l2_skip = 0
    records: list[dict] = []

    print(f"{'='*70}")
    print(f"  Total: {total}  |  L2: {'enabled' if orchestrator else 'skipped'}")
    print(f"{'='*70}\n")

    for i, row in enumerate(rows, 1):
        store     = row["지점코드(혹은 지점명)"]
        date      = row["조회 일자(가상)"]
        question  = row["질문"]
        gt_sql    = row["쿼리문"]
        expected  = str(row["응답"]).strip()
        store_code = store.split("/")[0].strip()

        keyword = row.get("키워드", "")
        llm_ans = row.get("LLM이 점주에게 보여줄 답변", "")
        record: dict = {
            "no": i,
            "L1_DB정합성": "",
            "L2_숫자정합성": "-" if (skip_l2 or orchestrator is None) else "",
            "지점코드": store,
            "조회일자": date,
            "질문": question,
            "키워드": keyword,
            "조회테이블": row["조회 대상 테이블"],
            "기댓값(DB응답)": expected,
            "L1_실제DB값": "",
            "LLM예상답변": llm_ans,
            "L2_Agent응답텍스트": "",
            "L2_불일치숫자": "",
            "처리경로": "",
            "쿼리유형": "",
            "비고": "",
            "L2_Agent생성SQL": "",
            "쿼리문": gt_sql,
        }

        # ── L1 ──────────────────────────────────────
        l1_status, l1_actual = evaluate_l1(gt_sql, expected, db_url)
        record["L1_DB정합성"] = l1_status
        record["L1_실제DB값"] = l1_actual
        if l1_status == "APP":
            record["비고"] = "앱 레벨 후처리 필요"
        if l1_status == "O":    l1_pass += 1
        elif l1_status == "X":  l1_fail += 1
        elif l1_status == "APP": l1_app += 1
        else:                   l1_err += 1

        # ── L2 ──────────────────────────────────────
        if orchestrator:
            try:
                os.environ["SQL_REFERENCE_DATE"] = date
                agent_resp = await orchestrator.handle_request(
                    question, context={"store_id": store_code}
                )
                answer_text = extract_answer_text(agent_resp)
                agent_sql   = extract_agent_sql(agent_resp)

                try:
                    db_rows = run_sql(gt_sql, db_url)
                except Exception:
                    db_rows = []

                l2_status, l2_unexpected = evaluate_l2_numeric(answer_text, db_rows, question)

                record.update({
                    "L2_Agent응답텍스트": answer_text,
                    "L2_Agent생성SQL": agent_sql,
                    "L2_숫자정합성": l2_status,
                    "L2_불일치숫자": l2_unexpected,
                    "처리경로": agent_resp.get("processing_route", "") if isinstance(agent_resp, dict) else "",
                    "쿼리유형": agent_resp.get("query_type", "") if isinstance(agent_resp, dict) else "",
                })
                if l2_status == "O": l2_pass += 1
                else:                l2_fail += 1

            except Exception as e:
                record["L2_숫자정합성"] = "ERROR"
                record["비고"] = str(e)
                l2_fail += 1
        else:
            l2_skip += 1

        records.append(record)

        l2_mark = record["L2_숫자정합성"]
        print(f"[{i:02d}] L1={l1_status:5s} L2={l2_mark:5s}  {store_code} | {date} | {question[:30]}")

    # 요약 행 추가
    l2_summary = f"PASS={l2_pass} / FAIL={l2_fail} / SKIP={l2_skip}" if not skip_l2 else "미실행"
    records.append({
        "no": "",
        "L1_DB정합성": f"PASS={l1_pass} / FAIL={l1_fail} / APP={l1_app} / ERROR={l1_err}",
        "L2_숫자정합성": l2_summary,
        "지점코드": "[요약]",
        "조회일자": "",
        "질문": f"전체 {total}건",
        "키워드": "", "조회테이블": "", "기댓값(DB응답)": "", "L1_실제DB값": "",
        "LLM예상답변": "", "L2_Agent응답텍스트": "", "L2_불일치숫자": "",
        "처리경로": "", "쿼리유형": "", "비고": "", "L2_Agent생성SQL": "", "쿼리문": "",
    })

    with open(CSV_OUTPUT, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(records)

    print(f"\n{'='*70}")
    print(f"  L1: total {total} | PASS {l1_pass} | FAIL {l1_fail} | APP {l1_app} | ERROR {l1_err}")
    if not skip_l2:
        l2_total = l2_pass + l2_fail
        print(f"  L2: total {l2_total} | PASS {l2_pass} | FAIL {l2_fail} | SKIP {l2_skip}")
    print(f"  Saved: {CSV_OUTPUT}")
    print(f"{'='*70}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent ground-truth 정합성 평가 (L1+L2)")
    parser.add_argument("--store_id", help="지점코드 필터 (예: POC_001)")
    parser.add_argument("--date", help="조회일자 필터 (예: 2025-12-04)")
    parser.add_argument("--limit", type=int, help="최대 평가 건수 (빠른 테스트용)")
    parser.add_argument("--skip-l2", action="store_true", help="L2(Agent 호출) 건너뛰기")
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", _DEFAULT_DB_URL),
        help="PostgreSQL 접속 URL",
    )
    args = parser.parse_args()

    asyncio.run(run_evaluation(
        store_id_filter=args.store_id,
        date_filter=args.date,
        limit=args.limit,
        skip_l2=args.skip_l2,
        db_url=args.db_url,
    ))


if __name__ == "__main__":
    main()
