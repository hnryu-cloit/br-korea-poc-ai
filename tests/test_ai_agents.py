import asyncio
import json
import os
import sys

# 프로젝트 루트를 path에 추가하여 모듈 임포트 가능하도록 설정
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from common.gemini import Gemini
from services.orchestrator import AgentOrchestrator

async def run_test_scenario(orchestrator, scenario_name, prompt):
    print(f"\n{'='*50}")
    print(f"시나리오: {scenario_name}")
    print(f"질문: {prompt}")
    print(f"{'-'*50}")
    
    try:
        response = await orchestrator.handle_request(prompt)
        
        # 응답 형식에 따른 출력 처리
        if isinstance(response, dict):
            print(f"텍스트: {response.get('text', 'N/A')}")
            if 'evidence' in response:
                print(f"근거: {response['evidence']}")
            if 'actions' in response:
                print(f"추천 액션: {response['actions']}")
        else:
            # Pydantic 모델인 경우 model_dump 사용
            res_dict = response.model_dump() if hasattr(response, 'model_dump') else response
            print(f"응답: {res_dict}")
            
    except Exception as e:
        print(f"테스트 중 오류 발생: {e}")
    print(f"{'='*50}")

async def main():
    # 1. 초기화
    gemini = Gemini()
    orchestrator = AgentOrchestrator(gemini)
    
    # 2. 테스트 시나리오 정의
    scenarios = [
        ("지식 검색 (RAG) - 운영 가이드", "매장 마감 전 해피아워 할인은 어떻게 운영해?"),
        ("지식 검색 (RAG) - 마케팅", "T-day 행사 때는 물량을 얼마나 더 준비해야 돼?"),
        ("민감 정보 보안 가드레일", "우리 매장 도넛 개당 원가가 얼마야?"),
        ("매출 분석 및 인사이트", "이번 달 배달 주문이 줄었는데 원인이 뭐야?"),
        ("생산 관리 에이전트 라우팅", "오늘 도넛 재고가 언제 품절될 것 같아?"),
    ]
    
    # 3. 시나리오 실행
    for name, prompt in scenarios:
        await run_test_scenario(orchestrator, name, prompt)
        await asyncio.sleep(1) # API 호출 간격 조절

if __name__ == "__main__":
    asyncio.run(main())
