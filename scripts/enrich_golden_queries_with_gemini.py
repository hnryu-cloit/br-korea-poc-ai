from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

AI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_ROOT))

from common.gemini import Gemini

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich golden queries with intent_id/synonyms using Gemini")
    parser.add_argument(
        "--csv",
        type=str,
        default=str(AI_ROOT.parent / "br-korea-poc-backend" / "docs" / "golden-queries.csv"),
        help="Target golden query CSV path",
    )
    parser.add_argument("--limit", type=int, default=30, help="Number of rows to enrich")
    parser.add_argument(
        "--domain",
        type=str,
        choices=["sales", "production", "ordering", "all"],
        default="all",
        help="Filter by domain",
    )
    parser.add_argument("--sleep", type=float, default=0.3, help="Sleep seconds between calls")
    return parser.parse_args()


def map_domain(agent_name: str) -> str:
    text = (agent_name or "").strip()
    if "생산" in text:
        return "production"
    if "주문" in text or "발주" in text:
        return "ordering"
    return "sales"


def normalize_intent_id(intent_id: str, fallback: str, domain: str) -> str:
    token = (intent_id or "").strip().lower()
    token = token.replace(" ", "_").replace("-", "_")
    token = "".join(ch for ch in token if ch.isalnum() or ch == "_")
    if not token:
        token = fallback
    if not token.startswith(f"{domain}_"):
        token = f"{domain}_{token}"
    return token


def build_prompt(row: dict[str, Any], domain: str) -> str:
    return f"""
다음 골든쿼리 문항을 패턴 매칭용 메타데이터로 변환하세요.

[도메인]
{domain}

[질문번호]
{row.get('질문번호','')}

[질문]
{row.get('질문','')}

[테이블/컬럼]
{row.get('테이블/컬럼','')}

출력 규칙:
- intent_id: 영문 snake_case, 3~6 토큰
- synonyms: 사용자 표현 다양화용 한국어 문장/구 6개 이하
- synonyms에는 날짜/상품/수량 변수 치환 가능한 표현 포함
- JSON으로만 출력

출력 형식:
{{
  "intent_id": "example_intent_id",
  "synonyms": ["동의어1", "동의어2", "동의어3"]
}}
""".strip()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        return 1

    gemini = Gemini()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    for required in ("의도ID", "동의어"):
        if required not in fieldnames:
            fieldnames.append(required)

    target_indexes: list[int] = []
    for idx, row in enumerate(rows):
        if (row.get("가용여부") or "").strip() != "✅":
            continue
        domain = map_domain(str(row.get("에이전트") or ""))
        if args.domain != "all" and domain != args.domain:
            continue
        if str(row.get("의도ID") or "").strip() and str(row.get("동의어") or "").strip():
            continue
        target_indexes.append(idx)

    target_indexes = target_indexes[: max(0, args.limit)]
    if not target_indexes:
        print("No rows to enrich.")
        return 0

    print(f"Target rows: {len(target_indexes)}")
    updated = 0

    for seq, idx in enumerate(target_indexes, 1):
        row = rows[idx]
        domain = map_domain(str(row.get("에이전트") or ""))
        prompt = build_prompt(row, domain)
        fallback_intent = str(row.get("질문번호") or f"row_{idx}").strip().replace("-", "_")

        try:
            raw = gemini.call_gemini_text(prompt, response_type="application/json")
            data = json.loads(raw) if isinstance(raw, str) else raw

            intent_id = normalize_intent_id(str(data.get("intent_id") or ""), fallback_intent, domain)
            synonyms = data.get("synonyms", [])
            if not isinstance(synonyms, list):
                synonyms = []
            cleaned = [str(item).strip() for item in synonyms if str(item).strip()][:6]

            row["의도ID"] = intent_id
            row["동의어"] = " | ".join(cleaned)
            updated += 1
            print(f"[{seq}/{len(target_indexes)}] OK {row.get('질문번호')} -> {intent_id}")
        except Exception as exc:
            print(f"[{seq}/{len(target_indexes)}] FAIL {row.get('질문번호')}: {exc}")

        time.sleep(max(0.0, args.sleep))

    backup_path = csv_path.with_suffix(csv_path.suffix + ".bak")
    backup_path.write_text(csv_path.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    print(f"Updated rows: {updated}")
    print(f"Backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
