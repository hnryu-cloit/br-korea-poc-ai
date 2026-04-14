import os
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from common.logger import init_logger

logger = init_logger("dummy_provider")

class MockPayloadProvider:
    """
    [POC 시연용] FE의 입력 파라미터가 없거나 로그인 정보가 없을 때,
    DB의 가장 최근 날짜를 '오늘(Today)'로 설정하여 가짜(Mock) 요청 데이터를 생성합니다.
    """
    def __init__(self, target_store_id: str = "POC_030"):
        self.target_store_id = target_store_id
        db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5435/br_korea_poc")
        try:
            self.engine = create_engine(db_url)
        except Exception as e:
            logger.error(f"MockProvider DB Error: {e}")
            self.engine = None

    def generate_mock_request(self) -> dict:
        """
        1. DB에서 해당 매장의 마지막 매출 발생일을 찾음
        2. 그 날짜를 '오늘(target_date)'로, '14:23'을 '현재 시간'으로 강제 설정
        3. target_date - 1 까지의 과거 데이터와 target_date(14:00까지)의 부분 데이터 추출
        """
        if not self.engine:
            return {"error": "DB Connection Failed"}

        with self.engine.connect() as conn:
            # 1. 해당 매장의 가장 마지막 매출 발생일 조회
            query = text('SELECT MAX("SALE_DT") FROM "DAILY_STOR_ITEM" WHERE "MASKED_STOR_CD" = :store')
            max_date_str = str(conn.execute(query, {"store": self.target_store_id}).scalar())
            
            if not max_date_str or max_date_str == "None":
                max_date_str = "20260406" # Fallback

            # 시연 환경 설정 (타임머신 탑재)
            target_date = f"{max_date_str[:4]}-{max_date_str[4:6]}-{max_date_str[6:]}"
            current_sim_time = datetime.strptime(target_date, "%Y-%m-%d").replace(hour=14, minute=23)

            # --- 이 시점(current_sim_time) 기준으로 엄격하게 데이터를 잘라서 가져오는 로직 (생략: 백엔드 API에서 처리) ---
            # 본래 백엔드의 Repository 단에서 "current_time" 이전에 발생한 데이터만 조회하여 보내는 것이 가장 이상적입니다.
            # AI 서버는 백엔드가 엄격하게 잘라서(필터링해서) 보내준 데이터를 그대로 믿고 분석만 하면 됩니다.

            return {
                "store_id": self.target_store_id,
                "target_date": target_date,
                "current_time": current_sim_time,
                "context": {
                    "weather": "맑음",  # Mock
                    "note": f"이 데이터는 시연을 위해 {current_sim_time.strftime('%Y-%m-%d %H:%M')} 기준으로 타임머신이 적용된 데이터입니다."
                }
            }
