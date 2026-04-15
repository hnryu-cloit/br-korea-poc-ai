from __future__ import annotations

from typing import Any


class BasicEvaluator:
    """기본 응답 품질 평가기 — LLM 호출 없이 구조적 기준만으로 평가"""

    # 최소 근거 문장 수
    MIN_EVIDENCE = 1
    # 최소 액션 아이템 수
    MIN_ACTIONS = 1

    def evaluate(self, response: dict[str, Any]) -> dict[str, Any]:
        """
        응답 딕셔너리를 받아 기본 품질 지표 반환.
        반환값: {"passed": bool, "score": float, "issues": list[str]}
        """
        issues: list[str] = []

        text = response.get("text") or response.get("answer", {}).get("text", "")
        evidence = response.get("evidence") or response.get("answer", {}).get("evidence", [])
        actions = response.get("actions") or response.get("answer", {}).get("actions", [])

        if not text:
            issues.append("응답 텍스트 없음")
        if len(evidence) < self.MIN_EVIDENCE:
            issues.append(f"근거 부족 (최소 {self.MIN_EVIDENCE}개 필요)")
        if len(actions) < self.MIN_ACTIONS:
            issues.append(f"액션 아이템 부족 (최소 {self.MIN_ACTIONS}개 필요)")

        # 1점 만점 기준 가중 채점
        total = 3
        passed_count = total - len(issues)
        score = round(passed_count / total, 2)

        return {
            "passed": len(issues) == 0,
            "score": score,
            "issues": issues,
        }

    def evaluate_batch(self, responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """응답 목록 일괄 평가"""
        return [self.evaluate(r) for r in responses]