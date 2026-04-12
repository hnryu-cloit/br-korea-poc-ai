from __future__ import annotations

import json
import pandas as pd
from datetime import datetime, timedelta
from typing import Any, List, Dict

from schemas.contracts import OrderingOption, OrderOptionType, OrderingRecommendationRequest, OrderingRecommendationResponse
from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_ordering_reasoning_prompt
from .seasonality_engine import SeasonalityEngine

logger = init_logger("ordering_service")


class OrderingService:
    def __init__(self, gemini_client: Gemini, historical_order_df: pd.DataFrame = None, product_group_deadlines: dict = None, campaign_df: pd.DataFrame = None):
        """
        주문 관리 서비스 초기화
        :param gemini_client: Gemini API 클라이언트
        :param historical_order_df: 과거 주문 상세 데이터 (ORD_DTL 테이블 기반 DataFrame)
        :param product_group_deadlines: 제품 그룹별 마감 시간 딕셔너리
        """
        self.gemini = gemini_client
        self.historical_order_df = historical_order_df if historical_order_df is not None else pd.DataFrame()
        self.product_group_deadlines = product_group_deadlines if product_group_deadlines is not None else {}
        self.seasonality_engine = SeasonalityEngine(campaign_df if campaign_df is not None else pd.DataFrame())

    def set_historical_data(self, df: pd.DataFrame):
        """과거 데이터를 외부(DataLoader 등)에서 로드하여 주입"""
        self.historical_order_df = df

    def _get_historical_qty(self, store_id: str, target_date_str: str, days_delta: int = 7, item_nm: str = None) -> int:
        """실제 과거 주문 데이터를 조회하며, 부재 시 비즈니스 룰에 따른 보정 수량 반환"""
        try:
            target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
            past_dt = target_dt - timedelta(days=days_delta)
            
            past_date_fmt1 = past_dt.strftime("%Y%m%d")
            past_date_fmt2 = past_dt.strftime("%Y-%m-%d")
            
            if self.historical_order_df.empty:
                return 0

            df = self.historical_order_df
            mask_store = (df['MASKED_STOR_CD'].astype(str) == str(store_id))
            date_col = df['DLV_DT'].astype(str)
            mask_date = ((date_col == past_date_fmt1) | (date_col == past_date_fmt2))
            
            if item_nm and 'ITEM_NM' in df.columns:
                mask_item = (df['ITEM_NM'] == item_nm)
                
                exact_match = df[mask_store & mask_date & mask_item]
                if not exact_match.empty:
                    result_qty = int(exact_match['ORD_QTY'].sum())
                    logger.info(f"[{item_nm}] 매장 실 데이터 기반 수량: {result_qty}")
                    return result_qty

                if not df[mask_item].empty:
                    logger.info(f"[{item_nm}] 매장 데이터 부재 -> 타 매장 평균 수량 적용")
                    other_stores_date = df[mask_date & mask_item]
                    
                    if not other_stores_date.empty:
                        avg_qty = other_stores_date.groupby('MASKED_STOR_CD')['ORD_QTY'].sum().mean()
                        return int(avg_qty) if pd.notna(avg_qty) else 0
                    else:
                        avg_qty = df[mask_item].groupby(['MASKED_STOR_CD', 'DLV_DT'])['ORD_QTY'].sum().mean()
                        return int(avg_qty) if pd.notna(avg_qty) else 0

                logger.info(f"[{item_nm}] 전체 데이터 부재(신제품) -> 타 제품 평균 기반 신제품 가중치(1.2x) 적용")
                store_date_avg = df[mask_store & mask_date]
                if not store_date_avg.empty:
                    avg_qty = store_date_avg.groupby('ITEM_NM')['ORD_QTY'].sum().mean() * 1.2
                    return int(avg_qty) if pd.notna(avg_qty) else 0
                
                global_avg = df.groupby(['MASKED_STOR_CD', 'ITEM_NM', 'DLV_DT'])['ORD_QTY'].sum().mean()
                return int(global_avg * 1.2) if pd.notna(global_avg) else 150
            
            past_data = df[mask_store & mask_date]
            if not past_data.empty:
                return int(past_data['ORD_QTY'].sum())
            
        except Exception as e:
            logger.error(f"Error querying historical data: {e}")

        return 0

    def check_and_trigger_push_notifications(self, current_time: datetime, store_ids: List[str]) -> List[Dict[str, str]]:
        """주기적으로 실행되어, 각 제품 그룹별 마감 20분 전이 된 경우 해당 매장들에게 발송할 Push 알림 목록 반환"""
        triggered_pushes = []
        
        if not self.product_group_deadlines:
            logger.warning("제품 그룹별 마감 시간 정보가 없어 트리거 로직을 건너뜁니다.")
            return triggered_pushes

        for group_name, deadline_str in self.product_group_deadlines.items():
            try:
                deadline_time = datetime.strptime(deadline_str, "%H:%M").time()
                deadline_dt = datetime.combine(current_time.date(), deadline_time)
                
                target_trigger_time = deadline_dt - timedelta(minutes=20)
                
                if current_time.hour == target_trigger_time.hour and current_time.minute == target_trigger_time.minute:
                    logger.info(f"[{group_name}] 마감 20분 전 도달. (마감: {deadline_str}) - 알림 트리거 발동")
                    
                    for store_id in store_ids:
                        push_msg = self.generate_push_event(store_id, group_name)
                        triggered_pushes.append(push_msg)
            except Exception as e:
                logger.error(f"마감 시간 파싱 오류 ({group_name} - {deadline_str}): {e}")

        return triggered_pushes

    def generate_push_event(self, store_id: str, product_group: str) -> Dict[str, str]:
        """STEP 1 & 2: 제품 그룹별 마감시간 20분 전 주문 마감 임박 PUSH 발송 및 라우팅 정보"""
        deadline = self.product_group_deadlines.get(product_group, "마감 시간")
        
        return {
            "title": f"[{product_group}] 주문 마감 20분 전",
            "body": f"오늘 {product_group} 주문 마감({deadline})이 임박했습니다. AI 추천 옵션을 확인하여 주문 누락을 방지하세요!",
            "target_screen": "CHAT_ORDERING_AGENT",
            "store_id": store_id,
            "action_type": "NAVIGATE_AND_LOAD_OPTIONS",
            "product_group": product_group
        }

    def calculate_base_ordering_options(self, store_id: str, target_date: str, target_product: str = None) -> List[OrderingOption]:
        """STEP 3: 3가지 바로 주문 옵션(전주, 전전주, 전월 동요일) 수량 계산"""
        qty_last_week = self._get_historical_qty(store_id, target_date, 7, target_product)
        qty_two_weeks = self._get_historical_qty(store_id, target_date, 14, target_product)
        qty_last_month = self._get_historical_qty(store_id, target_date, 28, target_product)

        if qty_last_week == 0 and qty_two_weeks == 0 and qty_last_month == 0:
            logger.warning("All historical data are zero. Using simulation fallback.")
            qty_last_week, qty_two_weeks, qty_last_month = 150, 145, 160

        season_weight = self.seasonality_engine.get_weight(target_date, target_product)
        if season_weight != 1.0:
            logger.info(f"시즌성 가중치 적용: {season_weight} (date={target_date})")
            qty_last_week = round(qty_last_week * season_weight)
            qty_two_weeks = round(qty_two_weeks * season_weight)
            qty_last_month = round(qty_last_month * season_weight)

        return [
            OrderingOption(option_type=OrderOptionType.LAST_WEEK, recommended_qty=qty_last_week, reasoning="", expected_sales=qty_last_week, seasonality_weight=season_weight if season_weight != 1.0 else None),
            OrderingOption(option_type=OrderOptionType.TWO_WEEKS_AGO, recommended_qty=qty_two_weeks, reasoning="", expected_sales=qty_two_weeks, seasonality_weight=season_weight if season_weight != 1.0 else None),
            OrderingOption(option_type=OrderOptionType.LAST_MONTH, recommended_qty=qty_last_month, reasoning="", expected_sales=qty_last_month, seasonality_weight=season_weight if season_weight != 1.0 else None)
        ]

    def _append_special_event_option_if_needed(self, store_id: str, target_date: str, target_product: str, context: dict, options: List[OrderingOption]):
        """명절 등 특별 이벤트 시나리오를 위한 추가 옵션 계산 로직"""
        special_event = context.get("special_event")
        if special_event:
            qty_special = self._get_historical_qty(store_id, target_date, 365, target_product)
            if qty_special == 0: qty_special = int(options[0].recommended_qty * 1.5)
            options.append(
                OrderingOption(option_type=OrderOptionType.SPECIAL, recommended_qty=qty_special, reasoning="", expected_sales=qty_special)
            )

    def generate_ordering_insights_and_questions(self, store_id: str, target_date: str, context: dict, options: List[OrderingOption]) -> tuple[List[OrderingOption], str]:
        """STEP 4: 선택 옵션에 따른 특이사항 표기 및 AI 분석 (Gemini 활용)"""
        options_summary = "\n".join([f"- {o.option_type.name}: {o.recommended_qty}건" for o in options])
        context_str = ", ".join([f"{k}: {v}" for k, v in context.items()])
        
        prompt = create_ordering_reasoning_prompt(
            store_id=store_id,
            current_date=target_date,
            current_context=context_str,
            options_summary=options_summary
        )
        
        try:
            ai_raw_response = self.gemini.call_gemini_text(prompt, response_type="json")
            ai_data = ai_raw_response if isinstance(ai_raw_response, dict) else json.loads(ai_raw_response)
            
            summary_insight = ai_data.get("analysis_summary", "")
            closing_msg = ai_data.get("closing_message", "")
            
            for opt in options:
                for detail in ai_data.get("option_details", []):
                    if detail.get("option_type") == opt.option_type.name:
                        opt.reasoning = f"[{detail.get('impact_factor')}] {detail.get('description')}"
                        break
                if not opt.reasoning:
                    opt.reasoning = f"{opt.option_type.value} 기반 추천 수량입니다."

            full_summary = f"{summary_insight}\n\n{closing_msg}"
        except Exception as e:
            logger.error(f"LLM Reasoning failed: {e}")
            full_summary = "과거 주문 데이터를 바탕으로 산출된 옵션입니다. 매장 상황을 고려하여 선택해 주세요."
            for opt in options: opt.reasoning = f"{opt.option_type.value} 데이터 기반"

        return options, full_summary

    def recommend_ordering(self, payload: OrderingRecommendationRequest) -> OrderingRecommendationResponse:
        """API Endpoint 용 STEP 3 & 4 통합 실행"""
        logger.info(f"Processing Ordering Recommendation API for {payload.store_id} at {payload.target_date}")
        
        target_product = payload.current_context.get("target_product")

        options = self.calculate_base_ordering_options(payload.store_id, payload.target_date, target_product)
        self._append_special_event_option_if_needed(payload.store_id, payload.target_date, target_product, payload.current_context, options)

        enriched_options, summary_insight = self.generate_ordering_insights_and_questions(
            payload.store_id, payload.target_date, payload.current_context, options
        )

        return OrderingRecommendationResponse(
            store_id=payload.store_id,
            recommendations=enriched_options,
            summary_insight=summary_insight
        )
