from __future__ import annotations

from tests.grounded_consistency_utils import compare_answer_numbers_to_rows


def test_compare_answer_numbers_to_rows_passes_when_numbers_exist_in_rows():
    rows = [{"ITEM_NM": "A", "TOTAL_SALES": 120.0}, {"ITEM_NM": "B", "TOTAL_SALES": 80.0}]
    result = compare_answer_numbers_to_rows(
        "A 상품이 120개로 가장 높고 B 상품은 80개입니다.",
        rows,
        query="상위 2개 품목 알려줘",
    )
    assert result["is_consistent"] is True
    assert result["unexpected_numbers"] == []


def test_compare_answer_numbers_to_rows_flags_unexpected_numbers():
    rows = [{"ITEM_NM": "A", "TOTAL_SALES": 120.0}]
    result = compare_answer_numbers_to_rows(
        "A 상품이 120개이고 B 상품은 95개입니다.",
        rows,
        query="상위 2개 품목 알려줘",
    )
    assert result["is_consistent"] is False
    assert 95.0 in result["unexpected_numbers"]
