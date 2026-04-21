from __future__ import annotations

from typing import Any

from fastapi import Request


def build_error_detail(
    request: Request,
    *,
    error_code: str,
    message: str,
    retryable: bool,
) -> dict[str, Any]:
    trace_id = getattr(request.state, "request_id", None)
    return {
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
        "trace_id": trace_id,
    }
