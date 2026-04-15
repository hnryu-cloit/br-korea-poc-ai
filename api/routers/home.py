import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Dict, Any
from datetime import datetime
import pandas as pd

from api.dependencies import verify_token, get_production_service, get_ordering_service, get_sales_service
try:
    from api.mock_payload_generator import get_demo_mock_payload
except ImportError:
    # Fallback if mock generator is not in the expected path
    def get_demo_mock_payload():
        return {
            "store_id": "POC_001",
            "target_date": "2024-01-15",
            "current_time": "2024-01-15 14:00:00"
        }
from services.dashboard_service import DashboardService
from schemas.dashboard import HomeDashboardResponse

router = APIRouter(prefix="/api/home", tags=["home"])
logger = logging.getLogger(__name__)

@router.post("/overview", response_model=HomeDashboardResponse)
async def get_home_overview(
    inventory_data: List[Dict[str, Any]],
    production_data: List[Dict[str, Any]],
    sales_data: List[Dict[str, Any]],
    store_production_data: List[Dict[str, Any]],
    prod_service = Depends(get_production_service),
    order_service = Depends(get_ordering_service),
    sales_service = Depends(get_sales_service)
) -> HomeDashboardResponse:
    """
    [HOME] 매장 홈 대시보드 통합 정보를 반환합니다.
    POC 시연을 위해 고정된 Mock 파라미터를 사용하여 제한된 과거 시점의 분석을 수행합니다.
    """
    try:
        # 1. 시연용 고정 JSON 페이로드 (로그인 정보 부재 대체)
        mock_payload = get_demo_mock_payload()
        
        # 문자열을 datetime 객체로 변환
        mock_payload["current_time"] = datetime.strptime(mock_payload["current_time"], "%Y-%m-%d %H:%M:%S")
        
        logger.info(f"POC Home Overview Request for Store: {mock_payload['store_id']} at {mock_payload['target_date']}")

        # 2. 통합 서비스 초기화 및 호출
        dash_service = DashboardService(prod_service, order_service, sales_service)
        
        raw_data = {
            "inventory_data": inventory_data,
            "production_data": production_data,
            "sales_data": sales_data,
            "store_production_data": store_production_data
        }
        
        result = await dash_service.get_home_overview(mock_payload, raw_data)
        return result
        
    except Exception as exc:
        logger.exception("홈 대시보드 생성 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="홈 화면 데이터를 불러오지 못했습니다.",
        ) from exc