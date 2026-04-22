from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.gemini import Gemini
from common.logger import init_logger
from services.orchestrator import AgentOrchestrator

logger = init_logger("live_agent_test")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one or more real questions through AgentOrchestrator."
    )
    parser.add_argument(
        "-q",
        "--query",
        action="append",
        help="Single question to send. Repeat for multiple questions.",
    )
    parser.add_argument(
        "--query-file",
        type=str,
        help="UTF-8 text file with one question per line.",
    )
    parser.add_argument(
        "--store-id",
        type=str,
        default="POC_001",
        help="Store id injected into orchestrator context.",
    )
    parser.add_argument(
        "--as-of-date",
        type=str,
        help="Reference date to treat as 'today' for SQL generation. Format: YYYY-MM-DD.",
    )
    parser.add_argument(
        "--show-json",
        action="store_true",
        help="Print the full response JSON.",
    )
    return parser.parse_args()


def load_queries(args: argparse.Namespace) -> list[str]:
    queries: list[str] = []

    if args.query:
        queries.extend([q.strip() for q in args.query if q and q.strip()])

    if args.query_file:
        file_path = Path(args.query_file)
        if not file_path.exists():
            raise FileNotFoundError(f"Query file not found: {file_path}")
        lines = file_path.read_text(encoding="utf-8").splitlines()
        queries.extend([line.strip() for line in lines if line.strip()])

    return queries


def normalize_response(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    return {"raw_response": str(response)}


def compact_response(response: dict[str, Any]) -> dict[str, Any]:
    answer = response.get("answer")
    if not isinstance(answer, dict):
        answer = {
            "text": str(response.get("text", "")),
            "evidence": response.get("evidence", []) if isinstance(response.get("evidence"), list) else [],
            "actions": response.get("actions", []) if isinstance(response.get("actions"), list) else [],
        }

    grounding = response.get("grounding")
    if not isinstance(grounding, dict):
        grounding = {
            "keywords": response.get("keywords", []) if isinstance(response.get("keywords"), list) else [],
            "intent": response.get("intent"),
            "relevant_tables": response.get("relevant_tables", [])
            if isinstance(response.get("relevant_tables"), list)
            else [],
            "sql": response.get("sql"),
            "row_count": response.get("row_count"),
        }

    compact = {
        "answer": answer,
        "query_type": response.get("query_type"),
        "processing_route": response.get("processing_route"),
        "queried_period": response.get("queried_period"),
        "grounding": grounding,
        "blocked": bool(response.get("blocked", False)),
        "masked_fields": response.get("masked_fields", []),
    }
    source_data_period = response.get("source_data_period")
    if source_data_period:
        compact["source_data_period"] = source_data_period

    lineage = response.get("data_lineage")
    if isinstance(lineage, list) and lineage:
        compact["data_lineage"] = [
            {
                "agent": item.get("agent"),
                "tables": item.get("tables", []),
                "row_count": item.get("row_count"),
            }
            for item in lineage
            if isinstance(item, dict)
        ]
    return compact


def extract_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("answer"), dict):
        return str(response["answer"].get("text", ""))
    return str(response.get("text", ""))


def extract_evidence(response: dict[str, Any]) -> list[str]:
    if isinstance(response.get("answer"), dict):
        evidence = response["answer"].get("evidence", [])
        return evidence if isinstance(evidence, list) else []
    evidence = response.get("evidence", [])
    return evidence if isinstance(evidence, list) else []


async def run_query(query: str, store_id: str, show_json: bool) -> None:
    print(f"\n[Question] {query}")
    print("-" * 80)

    gemini = Gemini()
    orchestrator = AgentOrchestrator(gemini)
    response = await orchestrator.handle_request(query, context={"store_id": store_id})
    data = compact_response(normalize_response(response))

    print("[Answer]")
    print(extract_text(data) or "(empty)")

    processing_route = data.get("processing_route")
    if processing_route:
        print(f"\n[Route] {processing_route}")

    query_type = data.get("query_type")
    if query_type:
        print(f"[Query Type] {query_type}")

    queried_period = data.get("queried_period")
    if isinstance(queried_period, dict) and queried_period.get("label"):
        print(f"[Queried Period] {queried_period['label']}")

    evidence = extract_evidence(data)
    if evidence:
        print("\n[Evidence]")
        for item in evidence:
            print(f"- {item}")

    lineage = data.get("data_lineage")
    if isinstance(lineage, list) and lineage:
        print("\n[Data Lineage]")
        for item in lineage:
            agent = item.get("agent", "-")
            tables = item.get("tables", [])
            query_sql = item.get("query", "")
            print(f"- agent={agent} tables={tables}")
            if query_sql:
                print(f"  sql={query_sql}")

    if show_json:
        print("\n[JSON]")
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


async def main() -> int:
    args = parse_args()
    load_dotenv()

    api_key = os.getenv("API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("API key is not set. Configure `API_KEY` or `GOOGLE_API_KEY` first.")
        return 1

    queries = load_queries(args)
    if args.as_of_date:
        os.environ["SQL_REFERENCE_DATE"] = args.as_of_date
    if not queries:
        user_query = input("질문 1개를 입력하세요: ").strip()
        if not user_query:
            print("질문이 비어 있습니다.")
            return 1
        queries = [user_query]

    for query in queries:
        try:
            await run_query(query, args.store_id, args.show_json)
        except Exception as exc:
            print(f"\n[Error] {query}")
            print(str(exc))
            logger.exception("live_agent_test failed for query=%s", query)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
