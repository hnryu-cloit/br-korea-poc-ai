from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import HTTPException, Request, status


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


@asynccontextmanager
async def router_error_handler(
    request: Request,
    *,
    error_code: str,
    message: str,
    log_message: str,
    retryable: bool = True,
) -> AsyncGenerator[None, None]:
    """라우터 공통 예외 처리 — Exception 발생 시 로깅 후 HTTP 500 반환"""
    try:
        yield
    except Exception as exc:
        logging.getLogger(__name__).exception(log_message)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                request,
                error_code=error_code,
                message=message,
                retryable=retryable,
            ),
        ) from exc
