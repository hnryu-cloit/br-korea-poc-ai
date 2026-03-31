from __future__ import annotations

import datetime
from typing import Any, List, Tuple, Dict

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from common.logger import init_logger

logger = init_logger("predictor")


class InventoryPredictor:
    """
    재고 및 생산 예측을 위한 ML/DL 모델 인터페이스입니다.
    데이터 전처리(Feature Engineering)와 모델 추론을 담당합니다.
    """

    def _prepare_features(self, history: List[dict[str, Any]]) -> pd.DataFrame:
        """
        입력 데이터를 ML 모델용 피처로 변환합니다.
        """
        df = pd.DataFrame(history)
        
        # 시간 기반 피처 생성 (임시)
        df['ts'] = range(len(df))
        
        # 판매 속도 (Sales Velocity) 계산: 이전 스텝 대비 판매량 변화
        if 'sales' in df.columns:
            df['sales_velocity'] = df['sales'].diff().fillna(0)
        else:
            df['sales_velocity'] = 0
            
        # 재고 소진 속도 (Burn Rate)
        if 'stock' in df.columns:
            df['burn_rate'] = df['stock'].diff().fillna(0)
            
        return df

    def predict_next_stock(self, history: List[dict[str, Any]], current_stock: int, forecast_steps: int = 1) -> Tuple[float, float]:
        """
        고도화된 피처를 기반으로 미래 재고를 예측합니다.
        """
        if not history or len(history) < 5:
            logger.warning("예측을 위한 데이터가 부족합니다. 최소 5개 이상의 히스토리가 필요합니다.")
            return float(current_stock), 0.3

        try:
            df = self._prepare_features(history)
            
            # 피처 선택: 시간(ts), 이전 판매량, 판매 속도 등
            # POC 수준에서는 ts와 sales_velocity를 주 피처로 사용
            features = ['ts', 'sales_velocity']
            X = df[features].values
            y = df['stock'].values
            
            model = LinearRegression()
            model.fit(X, y)
            
            # 미래 시점 피처 구성
            last_ts = df['ts'].iloc[-1]
            last_velocity = df['sales_velocity'].iloc[-1]
            
            # 1시간 후 예측 (미래 ts와 현재 판매 속도 유지 가정)
            future_X = np.array([[last_ts + forecast_steps, last_velocity]])
            prediction = model.predict(future_X)[0]
            
            # 결정계수(R^2)를 기반으로 신뢰도 계산
            r_squared = model.score(X, y)
            confidence = max(0.1, min(0.95, r_squared))
            
            # 예측값이 음수일 경우 0으로 보정
            predicted_value = max(0.0, float(prediction))
            
            logger.info(f"재고 예측 완료: 현재 {current_stock} -> 1시간 후 {predicted_value:.2f} (신뢰도: {confidence:.2f})")
            return predicted_value, confidence
            
        except Exception as e:
            logger.error(f"예측 도중 오류 발생: {e}")
            return float(current_stock), 0.0


class QueryClassifier:
    """
    자연어 질의 분류기 (ML/DL 기반 의도 파악)
    """
    def __init__(self):
        # 민감 정보 및 도메인 분류를 위한 키워드 기반 가중치 (향후 BERT 모델로 대체 가능)
        self.intent_map = {
            "SENSITIVE": ["원가", "이익", "수익", "본사", "비밀", "손익", "마진"],
            "PRODUCTION": ["생산", "재고", "품절", "도넛", "해동", "베이킹"],
            "ORDERING": ["주문", "발주", "추천", "얼마나", "수량"],
            "ANALYSIS": ["왜", "원인", "이유", "분석", "비교", "차이", "성과"]
        }

    def classify(self, text: str) -> str:
        """
        입력 텍스트를 분석하여 의도(Intent)를 분류합니다.
        """
        text = text.lower().replace(" ", "")
        
        # 1. 민감 정보 우선 탐지 (보안 정책)
        if any(word in text for word in self.intent_map["SENSITIVE"]):
            logger.info(f"민감 정보 감지됨: '{text}'")
            return "SENSITIVE"
        
        # 2. 도메인 분류
        if any(word in text for word in self.intent_map["PRODUCTION"]):
            return "PRODUCTION"
        
        if any(word in text for word in self.intent_map["ORDERING"]):
            return "ORDERING"
        
        if any(word in text for word in self.intent_map["ANALYSIS"]):
            return "ANALYSIS"
        
        return "GENERAL"
