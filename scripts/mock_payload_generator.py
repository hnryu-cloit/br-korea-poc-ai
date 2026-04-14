import os
from datetime import datetime, timedelta

def get_demo_mock_payload() -> dict:
    """
    [POC 시연 전용]
    FE에서 로그인/날짜 선택 없이 홈 화면 진입 시, 백엔드가 받게 되는 가상의 JSON 요청(Request) 데이터입니다.
    
    요구사항 반영:
    - 기준 날짜(target_date)는 DB에 존재하는 마지막 데이터 날짜로 가정합니다. (여기선 2026-04-06)
    - 현재 시간(current_time)은 기준 날짜의 특정 시점(예: 오후 2시 23분)으로 고정합니다.
    - 백엔드는 이 'target_date'와 'current_time'을 기준으로, 미래의 데이터(예: 오후 3시 매출, 내일 날씨)는 절대 조회하지 않도록 쿼리를 제한해야 합니다.
    """
    
    target_date_str = "2026-04-06" # 실제 DB의 마지막 데이터 날짜 (하드코딩)
    current_time_str = f"{target_date_str} 14:23:00"
    
    return {
        "store_id": "POC_030",
        "target_date": target_date_str,
        "current_time": current_time_str,
        "demo_context": {
            "weather": "흐림, 18도", # 오후 2시 23분 이전의 날씨로 제한된 데이터
            "event": "인근 대학교 개강",
            "is_demo_mode": True,
            "instruction": "과거 데이터는 target_date 전날까지만, 당일 데이터는 current_time 이전까지만 사용해야 함."
        }
    }
