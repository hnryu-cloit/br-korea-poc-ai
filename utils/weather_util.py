import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from common.logger import init_logger

logger = init_logger("weather_service")

# 기상청 단기예보(초단기실황) 격자(nx, ny) 매핑 (POC용 일부 지역 샘플)
KMA_GRID_MAPPING = {
    "서울특별시 강남구": (61, 126),
    "서울특별시 서초구": (61, 125),
    "서울특별시 종로구": (60, 127),
    "경기도 고양시": (57, 128),
    "경기도 성남시": (62, 123),
    "경기도 수원시": (62, 120),
    "인천광역시 연수구": (55, 123),
    "인천광역시 남동구": (56, 124),
    "부산광역시 해운대구": (99, 75),
    "경상북도 김천시": (80, 96),
    "제주특별자치도 제주시": (52, 38)
}

class WeatherService:
    def __init__(self, store_master_path: str = None):
        # .env 파일에 KMA_API_KEY 이름으로 공공데이터포털 디코딩 키를 넣어야 실제 동작합니다.
        self.api_key = os.getenv("KMA_API_KEY")
        # 기상청 단기예보(초단기실황) API 엔드포인트
        self.base_url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"
        
        # 매장 마스터 데이터 로드
        self.store_master_df = pd.DataFrame()
        if store_master_path and os.path.exists(store_master_path):
            try:
                self.store_master_df = pd.read_csv(store_master_path)
            except Exception as e:
                logger.error(f"매장 마스터 로드 실패: {e}")

    def get_store_location(self, store_id: str) -> tuple[str, str, int, int]:
        """
        매장 코드로 시도, 지역(시군구) 정보를 조회하고 기상청 X, Y 좌표를 반환
        """
        sido, region = "서울특별시", "강남구" # 기본값
        nx, ny = 61, 126
        
        if not self.store_master_df.empty and 'masked_stor_cd' in self.store_master_df.columns:
            store_info = self.store_master_df[self.store_master_df['masked_stor_cd'] == store_id]
            if not store_info.empty:
                sido = str(store_info.iloc[0].get('sido', sido)).strip()
                region = str(store_info.iloc[0].get('region', region)).strip()
                
        # Sido + Region 조합으로 Grid 매핑 조회
        location_key = f"{sido} {region}"
        if location_key in KMA_GRID_MAPPING:
            nx, ny = KMA_GRID_MAPPING[location_key]
            logger.info(f"[{store_id}] 위치 조회 매핑 성공: {location_key} -> (nx:{nx}, ny:{ny})")
        else:
            logger.warning(f"[{store_id}] 위치({location_key})에 해당하는 기상청 좌표 맵핑이 없어 기본값(서울 강남구)을 사용합니다.")
            location_key = f"{location_key} (기본값 서울 강남구 대체)"
            
        return sido, region, nx, ny, location_key

    def get_weather_by_store(self, store_id: str) -> str:
        """매장 코드를 기반으로 위치를 조회하고 실시간 날씨를 반환"""
        sido, region, nx, ny, loc_key = self.get_store_location(store_id)
        weather_desc = self.get_realtime_weather(nx=nx, ny=ny)
        return f"[{loc_key}] {weather_desc}"

    def get_realtime_weather(self, nx: int = 60, ny: int = 127) -> str:
        """
        기상청 초단기실황 API 호출 (최근 1시간~3시간 이내 가장 최신 실황 데이터)
        * 기본값 (nx=60, ny=127)은 서울특별시 좌표입니다.
        """
        if not self.api_key:
            logger.warning("KMA_API_KEY(기상청 API 키)가 설정되지 않아, 임시 시뮬레이션 날씨 데이터를 반환합니다.")
            return "맑음(강수없음), 기온 22.0℃"

        # 기상청 초단기실황은 매시간 40분에 생성됨. 
        # 안전하게 최근 생성된 데이터를 가져오기 위해 시간 계산
        now = datetime.now()
        if now.minute < 40:
            # 40분 이전이면 이전 시간대의 데이터를 조회
            now = now - timedelta(hours=1)

        base_date = now.strftime("%Y%m%d")
        base_time = now.strftime("%H00") # 정시 기준

        params = {
            "ServiceKey": self.api_key,
            "pageNo": "1",
            "numOfRows": "100",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(nx),
            "ny": str(ny)
        }

        try:
            response = requests.get(self.base_url, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                result_code = data.get("response", {}).get("header", {}).get("resultCode")
                
                if result_code == "00":
                    items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
                    weather_data = {item["category"]: item["obsrValue"] for item in items}

                    # T1H: 기온(℃), PTY: 강수형태, REH: 습도(%)
                    temp = weather_data.get("T1H", "알수없음")
                    pty_code = weather_data.get("PTY", "0")
                    humidity = weather_data.get("REH", "0")
                    
                    # 강수형태 코드 매핑
                    pty_map = {
                        "0": "맑음/흐림(강수없음)", "1": "비", "2": "비/눈", 
                        "3": "눈", "4": "소나기", "5": "빗방울", 
                        "6": "빗방울/눈날림", "7": "눈날림"
                    }
                    pty_str = pty_map.get(pty_code, "알수없음")

                    return f"{pty_str}, 기온 {temp}℃, 습도 {humidity}%"
                else:
                    error_msg = data.get("response", {}).get("header", {}).get("resultMsg", "Unknown Error")
                    logger.error(f"Weather API Response Error: {error_msg}")
                    return "날씨 정보 연동 실패 (기상청 응답 오류)"
            else:
                logger.error(f"Weather API HTTP Error: {response.status_code}")
                return "날씨 조회 실패 (HTTP 에러)"
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Weather API Network Exception: {e}")
            return "날씨 조회 실패 (네트워크 에러)"
        except Exception as e:
            logger.error(f"Weather API Parsing Exception: {e}")
            return "날씨 조회 실패 (데이터 처리 에러)"