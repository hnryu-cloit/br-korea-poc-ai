"""LLM 호출 메타데이터 로거 - 민감정보 마스킹 포함."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# (패턴, 대체 문자열)
_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\d{3}-\d{3,4}-\d{4}", "[PHONE]"),                          # 전화번호
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL]"),  # 이메일
    (r"\d{6}-[1-4]\d{6}", "[RRN]"),                                # 주민등록번호
    (r"\d{4}-\d{4}-\d{4}-\d{4}", "[CARD]"),                       # 카드번호
]


class LLMCallLogger:
    """LLM 호출 메타데이터만 기록하며 쿼리 원문은 저장하지 않습니다."""

    def mask_sensitive(self, text: str) -> str:
        """민감정보 패턴을 마스킹 토큰으로 치환합니다."""
        for pattern, replacement in _SENSITIVE_PATTERNS:
            text = re.sub(pattern, replacement, text)
        return text

    def log_call(
        self,
        query_type: str,
        was_blocked: bool,
        tokens_used: int = 0,
        store_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """쿼리 유형, 차단 여부, 토큰 수만 기록합니다 (쿼리 원문 제외)."""
        masked_store = (
            f"store_***{store_id[-3:]}" if store_id and len(store_id) >= 3 else "store_***"
        )
        extra = f" error={error}" if error else ""
        logger.info(
            "LLM call | type=%s blocked=%s tokens=%d store=%s ts=%s%s",
            query_type,
            was_blocked,
            tokens_used,
            masked_store,
            datetime.utcnow().isoformat(),
            extra,
        )


_default_llm_logger = LLMCallLogger()


def get_llm_logger() -> LLMCallLogger:
    return _default_llm_logger