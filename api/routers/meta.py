from __future__ import annotations

from datetime import date

from fastapi import APIRouter

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/contract")
async def get_contract_versions() -> dict[str, object]:
    """백엔드- AI 간 계약 버전 메타정보를 반환합니다."""
    return {
        "contract_version": "2026-04-21",
        "updated_at": str(date.today()),
        "interfaces": {
            "sales_query": "v2",
            "error_detail": "v1",
            "ordering_deadline_alert_batch": "v1",
        },
    }
