from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SalesQueryRequest(BaseModel):
    prompt: str


class GenerationResponse(BaseModel):
    status: str
    result: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None