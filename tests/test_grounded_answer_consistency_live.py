from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.grounded_consistency_utils import (
    compare_answer_numbers_to_rows,
    evaluate_with_optional_llm_judge,
    load_question_set,
    rerun_sql,
    run_question_case,
)


QUESTION_SET_PATH = Path(__file__).with_name("grounded_question_set.json")
QUESTION_CASES = load_question_set(QUESTION_SET_PATH)


def _live_env_ready() -> bool:
    return bool(os.getenv("DATABASE_URL")) and bool(os.getenv("API_KEY"))


@pytest.mark.skipif(not _live_env_ready(), reason="DATABASE_URL and API_KEY are required for live consistency checks")
@pytest.mark.parametrize("case", QUESTION_CASES, ids=[case.id for case in QUESTION_CASES])
def test_grounded_answers_match_db(case):
    from common.gemini import Gemini

    gemini = Gemini()
    result = run_question_case(case, gemini)

    assert result.get("text"), f"No answer text returned for {case.id}"
    assert result.get("sql"), f"No SQL recorded for {case.id}"

    rows = rerun_sql(result["sql"], case.store_id, result.get("relevant_tables"))
    if case.expect_data:
        assert rows, f"Expected rows for {case.id}, but query returned none"

    numeric_check = compare_answer_numbers_to_rows(
        result["text"],
        rows,
        query=case.query,
    )
    assert numeric_check["is_consistent"], (
        f"Rule-based numeric mismatch for {case.id}: "
        f"unexpected_numbers={numeric_check['unexpected_numbers']} "
        f"answer={result['text']}"
    )

    llm_judge = evaluate_with_optional_llm_judge(result["text"], rows, gemini)
    if llm_judge is not None:
        assert llm_judge["is_consistent"] is True, f"LLM judge mismatch for {case.id}: {llm_judge}"
