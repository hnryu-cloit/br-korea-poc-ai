import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import verify_token, get_production_service, get_ordering_service
from typing import List, Dict, Any
import pandas as pd
from schemas.contracts import (
    ProductionStatusRequest, ProductionAlarmResponse,
    OrderingRecommendationRequest, OrderingRecommendationResponse,
    SimulationRequest, SimulationReportResponse
)

# ... (기존 임포트 유지)

@router.post("/production/simulation", response_model=SimulationReportResponse, dependencies=[Depends(verify_token)])
async def get_production_simulation(
    payload: SimulationRequest,
    # 실제 연동 시에는 백엔드가 DB에서 조회한 데이터를 JSON 리스트 형태로 전달한다고 가정
    inventory_data: List[Dict[str, Any]],
    production_data: List[Dict[str, Any]],
    sales_data: List[Dict[str, Any]],
    service: ProductionService = Depends(get_production_service)
) -> SimulationReportResponse:
    """
    [FE/BE 연동] 과거 데이터를 기반으로 AI 생산 가이드 시뮬레이션 리포트를 생성합니다.
    """
    try:
        logger.info(f"시뮬레이션 요청: 매장 {payload.store_id}, 상품 {payload.item_id}")
        
        # 1. 수신된 데이터를 DataFrame으로 변환
        inv_df = pd.DataFrame(inventory_data)
        prod_df = pd.DataFrame(production_data)
        sales_df = pd.DataFrame(sales_data)
        
        # 2. 서비스 호출
        result = await asyncio.to_thread(
            service.get_simulation_report, 
            payload, inv_df, prod_df, sales_df
        )
        return result
    except Exception as exc:
        logger.exception("시뮬레이션 생성 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"시뮬레이션 생성 실패: {str(exc)}",
        ) from exc


@router.post("/ordering/recommend", response_model=OrderingRecommendationResponse, dependencies=[Depends(verify_token)])
async def recommend_ordering(
    payload: OrderingRecommendationRequest,
    service: OrderingService = Depends(get_ordering_service)
) -> OrderingRecommendationResponse:
    try:
        logger.info("주문 추천 요청: 매장 %s", payload.store_id)
        result = await asyncio.to_thread(service.recommend_ordering, payload)
        return result
    except Exception as exc:
        logger.exception("주문 추천 중 오류 발생")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="주문 추천에 실패했습니다.",
        ) from exc
