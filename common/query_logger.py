"""SQL 쿼리 실행 히스토리 로거 — 에이전트별 lineage 추적"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class QueryLogger:
    """에이전트별 SQL 실행 이력 메모리 기록"""

    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []

    def log_query(
        self,
        agent_name: str,
        tables: list[str],
        query: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self._history.append(
            {"agent": agent_name, "tables": tables, "query": query, "params": params or {}}
        )
        logger.debug("쿼리 기록 [%s] tables=%s", agent_name, tables)

    def get_history(self, agent_name: str | None = None) -> list[dict[str, Any]]:
        if agent_name is None:
            return list(self._history)
        return [h for h in self._history if h["agent"] == agent_name]

    def clear_history(self) -> None:
        self._history.clear()


query_logger = QueryLogger()
