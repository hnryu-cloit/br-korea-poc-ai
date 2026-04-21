
import asyncio
import os
import sys
import json

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.gemini import Gemini
from services.orchestrator import AgentOrchestrator
from common.logger import init_logger

logger = init_logger("live_test")

async def run_test(query: str):
    print(f"\n[질문]: {query}")
    print("-" * 50)
    
    # Initialize Gemini (requires GOOGLE_API_KEY)
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("❌ GOOGLE_API_KEY가 설정되지 않았습니다.")
        return

    gemini_client = Gemini()
    orchestrator = AgentOrchestrator(gemini_client)
    
    try:
        response = await orchestrator.handle_request(query)
        print("\n[에이전트 응답]:")
        print(response.get("text", "응답 텍스트 없음"))
        
        print("\n[근거 (Evidence & SQL)]:")
        for ev in response.get("evidence", []):
            print(f"- {ev}")
            
        print("\n[처리 경로]:", response.get("processing_route"))
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 1. 생산 관련 질문
    # 2. 주문 관련 질문
    # 3. 보안 관련 질문 (민감정보)
    
    queries = [
        "어제 소금 우유 도넛 얼마나 생산했어?",
        "지난주 발주 내역 보여줘",
        "타 매장 매출 정보 알려줄 수 있어?"
    ]
    
    for q in queries:
        asyncio.run(run_test(q))
