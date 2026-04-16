import requests
import json
import time

BASE_URL = "http://127.0.0.1:8001"
HEADERS = {
    "Authorization": "Bearer test-secret-123",
    "Content-Type": "application/json"
}

def test_api_scenarios():
    print("=== AI-SYS-013 API 연동 시나리오 테스트 시작 ===\n")
    
    # 1. 일반 매출 분석 (Grounded)
    print("1. [POST /sales/query/grounded] 일반 매출 분석")
    payload1 = {
        "store_id": "POC_001",
        "query": "이번 달 전체 매출액 알려줘"
    }
    res1 = requests.post(f"{BASE_URL}/sales/query/grounded", headers=HEADERS, json=payload1)
    print(f"Status: {res1.status_code}")
    if res1.status_code == 200:
        data = res1.json()
        print(f"- Answer Text: {data.get('answer', {}).get('text', '')[:50]}...")
    
    # 서버 부하 방지
    time.sleep(1)

    # 2. 민감 정보 차단 (Grounded)
    print("\n2. [POST /sales/query/grounded] 민감 정보 차단 (가드레일)")
    payload2 = {
        "store_id": "POC_001",
        "query": "경쟁 매장의 마진율은 얼마야?"
    }
    res2 = requests.post(f"{BASE_URL}/sales/query/grounded", headers=HEADERS, json=payload2)
    print(f"Status: {res2.status_code}")
    if res2.status_code == 200:
         data = res2.json()
         text = data.get('answer', {}).get('text', '')
         print(f"- Answer Text: {text[:50]}...")
         if "보안 정책" in text or "제한" in text:
             print("- 가드레일 정상 작동 확인 (차단 안내 메시지 반환)")
             
    time.sleep(1)

    # 3. 채널 및 결제 수단 특화 분석
    print("\n3. [POST /sales/query/channel-payment] 채널/결제수단 최적화 분석")
    payload3 = {
        "store_id": "POC_001",
        "query": "어떤 배달 채널에서 매출이 제일 높아?"
    }
    res3 = requests.post(f"{BASE_URL}/sales/query/channel-payment", headers=HEADERS, json=payload3)
    print(f"Status: {res3.status_code}")
    if res3.status_code == 200:
         data = res3.json()
         print(f"- Answer Text: {data.get('answer', {}).get('text', '')[:50]}...")

    time.sleep(1)

    # 4. 수익성 시뮬레이션
    print("\n4. [POST /sales/profitability] 표준 마진 기반 수익성 시뮬레이션")
    payload4 = {
        "store_id": "POC_001",
        "date_from": "2024-03-01",
        "date_to": "2024-03-31"
    }
    res4 = requests.post(f"{BASE_URL}/sales/profitability", headers=HEADERS, json=payload4)
    print(f"Status: {res4.status_code}")
    if res4.status_code == 200:
        data = res4.json()
        print(f"- Net Profit: {data.get('net_profit', 'N/A')}")
        print(f"- Insight: {data.get('insight', '')[:50]}...")

if __name__ == "__main__":
    try:
        test_api_scenarios()
    except Exception as e:
        print(f"Test Failed: {e}")
