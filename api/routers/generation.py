from fastapi import APIRouter, Depends

from api.dependencies import verify_token
from api.schemas import GenerationResponse
from src.pipeline.run import run_pipeline

router = APIRouter(prefix="/generation", tags=["generation"])


@router.post("", response_model=GenerationResponse, dependencies=[Depends(verify_token)])
async def generate() -> GenerationResponse:
    result = run_pipeline()
    return GenerationResponse(status="ok", result=result)