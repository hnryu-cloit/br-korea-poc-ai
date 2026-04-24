from __future__ import annotations

import json
from pathlib import Path

OUT = Path("tests/golden_query_holdout_cases_extra_100.json")

# 50 positives: ordering/sales/production paraphrases (not used in current holdout)
pos_templates = [
    ("ordering", "016-001-", "지난주 수동 발주 비중 높은 품목 {tail}"),
    ("ordering", "047-001-", "이번달 발주 수량 변동 큰 품목 {tail}"),
    ("ordering", "048-001-", "최근 30일 발주량 변동성 높은 SKU {tail}"),
    ("ordering", "051-001-", "오늘 발주 대비 확정 차이 큰 품목 {tail}"),
    ("ordering", "054-001-", "이번달 수동발주 확정률 비교 {tail}"),
    ("sales", "005-001-", "오늘 시간대별 매출 피크 {tail}"),
    ("sales", "006-001-", "지난달 대비 객단가 변화 {tail}"),
    ("sales", "018-001-", "이번주 요일별 매출 흐름 {tail}"),
    ("sales", "022-001-", "오늘 저마진 상품 우선 {tail}"),
    ("production", "019-001-", "최근 폐기 많은 품목 {tail}"),
    ("production", "020-001-", "생산 지연 잦은 품목 {tail}"),
    ("production", "021-001-", "이번주 재고 부족 품목 {tail}"),
    ("production", "009-001-", "어제 재고율 50% 미만 품목 수 {tail}"),
    ("production", "012-001-", "최근 30일 재고율 분포 변화 {tail}"),
    ("production", "041-001-", "판매 많은데 재고율 낮은 품목 {tail}"),
    ("production", "042-001-", "재고율 높은데 판매 저조 품목 {tail}"),
]

pos_tails = [
    "알려줘",
    "보여줘",
    "점검해줘",
    "핵심만 말해줘",
    "우선순위로 알려줘",
]

neg_templates = [
    ("sales", "인건비 비율 추이 분석해줘"),
    ("sales", "월세와 공과금 합계 보여줘"),
    ("sales", "뉴스 요약해줘"),
    ("sales", "환율 알려줘"),
    ("ordering", "와이파이 비밀번호 알려줘"),
    ("ordering", "본사 담당자 연락처 알려줘"),
    ("ordering", "쿠폰 문구 추천해줘"),
    ("ordering", "홍보 문안 작성해줘"),
    ("production", "직원 근태 이슈 알려줘"),
    ("production", "근로계약 갱신 대상 알려줘"),
    ("production", "교육 이수율 보여줘"),
    ("production", "주차장 요금 정책 알려줘"),
]

cases = []
# 50 positives
idx = 1
for domain, qid, base in pos_templates:
    for tail in pos_tails:
        cases.append(
            {
                "id": f"X-P-{idx:03d}",
                "query": base.format(tail=tail),
                "expected_domain": domain,
                "should_hit": True,
                "expected_query_id": qid,
            }
        )
        idx += 1

# 50 negatives
idx = 1
for i in range(50):
    domain, text = neg_templates[i % len(neg_templates)]
    cases.append(
        {
            "id": f"X-N-{idx:03d}",
            "query": text,
            "expected_domain": domain,
            "should_hit": False,
        }
    )
    idx += 1

OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"written: {OUT} ({len(cases)} cases)")
