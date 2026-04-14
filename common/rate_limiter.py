"""LLM API 호출용 인메모리 슬라이딩 윈도우 Rate Limiter."""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """스레드 안전한 슬라이딩 윈도우 Rate Limiter.

    기본값: 분당 최대 60회 호출 허용.
    """

    def __init__(self, max_calls: int = 60, window_seconds: int = 60) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._calls: dict[str, deque[datetime]] = {}

    def is_allowed(self, key: str = "global") -> bool:
        """호출 허용 여부 확인 및 호출 횟수 기록."""
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=self.window_seconds)
        with self._lock:
            if key not in self._calls:
                self._calls[key] = deque()
            q = self._calls[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_calls:
                logger.warning("Rate limit exceeded: key=%s (%d/%d)", key, len(q), self.max_calls)
                return False
            q.append(now)
            return True

    def get_remaining(self, key: str = "global") -> int:
        """현재 윈도우 내 남은 허용 호출 횟수."""
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=self.window_seconds)
        with self._lock:
            if key not in self._calls:
                return self.max_calls
            q = self._calls[key]
            while q and q[0] < cutoff:
                q.popleft()
            return max(0, self.max_calls - len(q))


_default_limiter = InMemoryRateLimiter(max_calls=60, window_seconds=60)


def get_rate_limiter() -> InMemoryRateLimiter:
    """싱글턴 Rate Limiter 인스턴스를 반환합니다."""
    return _default_limiter