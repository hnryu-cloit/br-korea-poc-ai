from typing import Any

from pydantic import BaseModel


class GenerationResponse(BaseModel):
    status: str
    result: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None