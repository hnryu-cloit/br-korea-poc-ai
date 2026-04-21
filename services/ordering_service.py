from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import pytz as _pytz
    _HAS_PYTZ = True
except ImportError:
    _HAS_PYTZ = False

import pandas as pd

from common.gemini import Gemini
from common.logger import init_logger
from common.prompt import create_ordering_reasoning_prompt
from schemas.contracts import (
    DeadlineAlertResponse,
    OrderingOption,
    OrderingRecommendationRequest,
    OrderingRecommendationResponse,
    OrderOptionType,
)

from .seasonality_engine import SeasonalityEngine

logger = init_logger(__name__)


class OrderingService:
    def __init__(
        self,
        gemini_client: Gemini,
        historical_order_df: pd.DataFrame = None,
        product_group_deadlines: dict = None,
        campaign_df: pd.DataFrame = None,
    ):
        """
        주문 관리 서비스 초기화
        :param gemini_client: Gemini API 클라이언트
        :param historical_order_df: 과거 주문 상세 데이터 (ORD_DTL 테이블 기반 DataFrame)
        :param product_group_deadlines: 제품 그룹별 마감 시간 딕셔너리
        """
        self.gemini = gemini_client
        self.historical_order_df = (
            historical_order_df if historical_order_df is not None else pd.DataFrame()
        )
        self.product_group_deadlines = (
            product_group_deadlines if product_group_deadlines is not None else {}
        )
        self.seasonality_engine = SeasonalityEngine(
            campaign_df if campaign_df is not None else pd.DataFrame()
        )

    def set_historical_data(self, df: pd.DataFrame):
        """과거 데이터를 외부(DataLoader 등)에서 로드하여 주입"""
        self.historical_order_df = df

    @staticmethod
    def _pick_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate in df.columns:
                return candidate
        upper_map = {column.upper(): column for column in df.columns}
        for candidate in candidates:
            resolved = upper_map.get(candidate.upper())
            if resolved:
                return resolved
        return None

    def _resolve_order_columns(self, df: pd.DataFrame) -> dict[str, str] | None:
        required_map = {
            "store": ["MASKED_STOR_CD", "STORE_ID", "STOR_CD", "store_id"],
            "date": ["DLV_DT", "SALE_DT", "ORD_DT", "date"],
            "qty": ["ORD_QTY", "SALE_QTY", "QTY", "qty"],
        }
        optional_map = {
            "item": ["ITEM_NM", "ITEM_NAME", "PRODUCT_NM", "item_name"],
        }

        resolved: dict[str, str] = {}
        for key, candidates in required_map.items():
            column = self._pick_first_existing_column(df, candidates)
            if column is None:
                return None
            resolved[key] = column
        for key, candidates in optional_map.items():
            column = self._pick_first_existing_column(df, candidates)
            if column is not None:
                resolved[key] = column
        return resolved

    def _get_historical_qty(
        self, store_id: str, target_date_str: str, days_delta: int = 7, item_nm: str = None
    ) -> int:
        """실제 과거 주문 데이터를 조회하며, 부재 시 비즈니스 룰에 따른 보정 수량 반환"""
        try:
            target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
            past_dt = target_dt - timedelta(days=days_delta)

            past_date_fmt1 = past_dt.strftime("%Y%m%d")
            past_date_fmt2 = past_dt.strftime("%Y-%m-%d")

            if self.historical_order_df.empty:
                return 0

            df = self.historical_order_df
            column_map = self._resolve_order_columns(df)
            if column_map is None:
                logger.warning("주문 이력 컬럼을 해석하지 못해 fallback 수량을 사용합니다.")
                return 0

            store_col = column_map["store"]
            date_source_col = column_map["date"]
            qty_col = column_map["qty"]
            item_col = column_map.get("item")

            mask_store = df[store_col].astype(str) == str(store_id)
            date_col = df[date_source_col].astype(str)
            mask_date = (date_col == past_date_fmt1) | (date_col == past_date_fmt2)

            if item_nm and item_col:
                mask_item = df[item_col] == item_nm

                exact_match = df[mask_store & mask_date & mask_item]
                if not exact_match.empty:
                    result_qty = int(
                        pd.to_numeric(exact_match[qty_col], errors="coerce").fillna(0).sum()
                    )
                    logger.info(f"[{item_nm}] 매장 실 데이터 기반 수량: {result_qty}")
                    return result_qty

                if not df[mask_item].empty:
                    logger.info(f"[{item_nm}] 매장 데이터 부재 -> 타 매장 평균 수량 적용")
                    other_stores_date = df[mask_date & mask_item]

                    if not other_stores_date.empty:
                        avg_qty = other_stores_date.groupby(store_col)[qty_col].sum().mean()
                        return int(avg_qty) if pd.notna(avg_qty) else 0
                    else:
                        avg_qty = (
                            df[mask_item]
                            .groupby([store_col, date_source_col])[qty_col]
                            .sum()
                            .mean()
                        )
                        return int(avg_qty) if pd.notna(avg_qty) else 0

                logger.info(
                    f"[{item_nm}] 전체 데이터 부재(신제품) -> 타 제품 평균 기반 신제품 가중치(1.2x) 적용"
                )
                store_date_avg = df[mask_store & mask_date]
                if not store_date_avg.empty:
                    group_col = item_col or store_col
                    avg_qty = store_date_avg.groupby(group_col)[qty_col].sum().mean() * 1.2
                    return int(avg_qty) if pd.notna(avg_qty) else 0

                group_cols = [store_col, date_source_col]
                if item_col:
                    group_cols.insert(1, item_col)
                global_avg = df.groupby(group_cols)[qty_col].sum().mean()
                return int(global_avg * 1.2) if pd.notna(global_avg) else 150

            past_data = df[mask_store & mask_date]
            if not past_data.empty:
                return int(pd.to_numeric(past_data[qty_col], errors="coerce").fillna(0).sum())

        except (ValueError, TypeError, KeyError) as e:
            logger.error(f"Error querying historical data: {e}")

        return 0

    def check_and_trigger_push_notifications(
        self, current_time: datetime, store_ids: list[str]
    ) -> list[dict[str, str]]:
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

                if (
                    current_time.hour == target_trigger_time.hour
                    and current_time.minute == target_trigger_time.minute
                ):
                    logger.info(
                        f"[{group_name}] 마감 20분 전 도달. (마감: {deadline_str}) - 알림 트리거 발동"
                    )

                    for store_id in store_ids:
                        push_msg = self.generate_push_event(store_id, group_name)
                        triggered_pushes.append(push_msg)
            except ValueError as e:
                logger.error(f"마감 시간 파싱 오류 ({group_name} - {deadline_str}): {e}")

        return triggered_pushes

    def generate_push_event(self, store_id: str, product_group: str) -> dict[str, str]:
        """STEP 1 & 2: 제품 그룹별 마감시간 20분 전 주문 마감 임박 PUSH 발송 및 라우팅 정보"""
        deadline = self.product_group_deadlines.get(product_group, "마감 시간")

        return {
            "title": f"[{product_group}] 주문 마감 20분 전",
            "body": f"오늘 {product_group} 주문 마감({deadline})이 임박했습니다. AI 추천 옵션을 확인하여 주문 누락을 방지하세요!",
            "target_screen": "CHAT_ORDERING_AGENT",
            "store_id": store_id,
            "action_type": "NAVIGATE_AND_LOAD_OPTIONS",
            "product_group": product_group,
        }

    @staticmethod
    def _build_option_id(option_type: OrderOptionType) -> str:
        return option_type.name.lower()

    @staticmethod
    def _build_option_title(option_type: OrderOptionType) -> str:
        return option_type.value

    @staticmethod
    def _extract_weather_summary(context: dict[str, Any]) -> str | None:
        weather = context.get("weather_summary") or context.get("weather")
        if weather:
            return str(weather)
        if context.get("is_rainy"):
            return "우천 가능성이 있어 배달/포장 수요 변화를 함께 확인했습니다."
        return None

    @staticmethod
    def _extract_trend_summary(
        context: dict[str, Any], options: list[OrderingOption]
    ) -> str | None:
        explicit = context.get("trend_summary")
        if explicit:
            return str(explicit)
        if not options:
            return None
        top_qty = max(option.recommended_qty for option in options)
        low_qty = min(option.recommended_qty for option in options)
        return f"추천 옵션 간 수량 편차는 {top_qty - low_qty}건이며 최근 패턴 변동성을 함께 반영했습니다."

    @staticmethod
    def _build_purpose_text(notification_entry: bool) -> str:
        if notification_entry:
            return "알림 기준으로 주문 추천안을 확인하고 누락 없이 최종 수량을 결정하세요."
        return "주문 누락을 방지하고 최적 수량을 선택하세요."

    @staticmethod
    def _build_caution_text() -> str:
        return "최종 주문 결정은 점주의 권한입니다. 추천 옵션은 보조 자료로만 활용해주세요."

    def _resolve_deadline(self, target_date: str, context: dict[str, Any]) -> tuple[int, int]:
        deadline_text = context.get("deadline_at") or context.get("deadline")
        if isinstance(deadline_text, str):
            try:
                hour_str, minute_str = deadline_text.split(":", 1)
                return int(hour_str), int(minute_str)
            except ValueError:
                logger.warning("deadline 문자열 파싱 실패: %s", deadline_text)

        if self.product_group_deadlines:
            first_deadline = sorted(self.product_group_deadlines.values())[0]
            try:
                hour_str, minute_str = first_deadline.split(":", 1)
                return int(hour_str), int(minute_str)
            except ValueError:
                logger.warning("product_group_deadlines 파싱 실패: %s", first_deadline)

        return 14, 0

    def _build_reasoning_metrics(
        self, option: OrderingOption, options: list[OrderingOption]
    ) -> list[dict[str, str]]:
        quantities = [candidate.recommended_qty for candidate in options]
        top_qty = max(quantities) if quantities else option.recommended_qty
        metrics: list[dict[str, str]] = [
            {"key": "recommended_qty", "value": f"{option.recommended_qty}건"},
            {"key": "expected_sales", "value": f"{option.expected_sales}건"},
        ]
        if option.seasonality_weight is not None:
            metrics.append(
                {"key": "seasonality_weight", "value": f"{option.seasonality_weight:.2f}x"}
            )
        if top_qty:
            ratio = round((option.recommended_qty / top_qty) * 100)
            metrics.append({"key": "relative_level", "value": f"상위안 대비 {ratio}%"})
        return metrics

    def _build_special_factors(self, context: dict[str, Any], option: OrderingOption) -> list[str]:
        factors: list[str] = []
        if context.get("is_campaign"):
            factors.append("캠페인 가중치 반영")
        if context.get("is_holiday"):
            factors.append("휴일 수요 변동 반영")
        if context.get("special_event"):
            factors.append(f"특수 이벤트 반영: {context['special_event']}")
        if option.seasonality_weight is not None and option.seasonality_weight != 1.0:
            factors.append(f"시즌성 보정 {option.seasonality_weight:.2f}x")
        weather_summary = self._extract_weather_summary(context)
        if weather_summary:
            factors.append("날씨 변수 반영")
        return factors

    def _build_items(self, context: dict[str, Any], option: OrderingOption) -> list[dict[str, Any]]:
        target_product = context.get("target_product")
        if target_product:
            return [
                {
                    "sku_id": None,
                    "sku_name": str(target_product),
                    "quantity": option.recommended_qty,
                    "note": "대표 타깃 품목 기준 권장 수량",
                }
            ]
        return []

    def _enrich_option_contract_fields(
        self, options: list[OrderingOption], context: dict[str, Any]
    ) -> None:
        for index, option in enumerate(options, start=1):
            option.option_id = self._build_option_id(option.option_type)
            option.title = self._build_option_title(option.option_type)
            option.basis = f"{option.option_type.value} 기준"
            option.description = (
                f"예상 판매 {option.expected_sales}건을 기준으로 산출한 주문안입니다."
            )
            option.recommended = index == 1
            option.reasoning_text = option.reasoning
            option.reasoning_metrics = self._build_reasoning_metrics(option, options)
            option.special_factors = self._build_special_factors(context, option)
            option.items = self._build_items(context, option)

    def calculate_base_ordering_options(
        self, store_id: str, target_date: str, target_product: str = None
    ) -> list[OrderingOption]:
        """STEP 3: 3가지 바로 주문 옵션(전주, 전전주, 전월 동요일) 수량 계산"""
        qty_last_week = self._get_historical_qty(store_id, target_date, 7, target_product)
        qty_two_weeks = self._get_historical_qty(store_id, target_date, 14, target_product)
        qty_last_month = self._get_historical_qty(store_id, target_date, 28, target_product)

        season_weight = self.seasonality_engine.get_weight(target_date, target_product)
        if season_weight != 1.0:
            logger.info(f"시즌성 가중치 적용: {season_weight} (date={target_date})")
            qty_last_week = round(qty_last_week * season_weight)
            qty_two_weeks = round(qty_two_weeks * season_weight)
            qty_last_month = round(qty_last_month * season_weight)

        return [
            OrderingOption(
                option_type=OrderOptionType.LAST_WEEK,
                recommended_qty=qty_last_week,
                reasoning="",
                expected_sales=qty_last_week,
                seasonality_weight=season_weight if season_weight != 1.0 else None,
            ),
            OrderingOption(
                option_type=OrderOptionType.TWO_WEEKS_AGO,
                recommended_qty=qty_two_weeks,
                reasoning="",
                expected_sales=qty_two_weeks,
                seasonality_weight=season_weight if season_weight != 1.0 else None,
            ),
            OrderingOption(
                option_type=OrderOptionType.LAST_MONTH,
                recommended_qty=qty_last_month,
                reasoning="",
                expected_sales=qty_last_month,
                seasonality_weight=season_weight if season_weight != 1.0 else None,
            ),
        ]

    def _append_special_event_option_if_needed(
        self,
        store_id: str,
        target_date: str,
        target_product: str,
        context: dict,
        options: list[OrderingOption],
    ):
        """명절 등 특별 이벤트 시나리오를 위한 추가 옵션 계산 로직"""
        special_event = context.get("special_event")
        if special_event:
            qty_special = self._get_historical_qty(store_id, target_date, 365, target_product)
            if qty_special <= 0:
                return
            options.append(
                OrderingOption(
                    option_type=OrderOptionType.SPECIAL,
                    recommended_qty=qty_special,
                    reasoning="",
                    expected_sales=qty_special,
                )
            )

    def generate_ordering_guidance(self, query: str) -> str:
        """주문 질의에 대한 운영 가이드를 생성합니다."""
        prompt = (
            f"다음 주문 관련 질의에 답변하세요: {query}\n\n"
            "주문 관리 관점에서 추천 수량, 마감 시간, 시즌성 정보를 포함해 한국어로 답변하세요."
        )
        try:
            return self.gemini.call_gemini_text(prompt)
        except (ValueError, TypeError, RuntimeError) as exc:
            logger.warning("주문 가이드 생성 실패: %s", exc)
            return "주문 관리 화면에서 3가지 추천 옵션을 확인하고 마감 시간 전에 주문해주세요."

    def generate_ordering_insights_and_questions(
        self, store_id: str, target_date: str, context: dict, options: list[OrderingOption]
    ) -> tuple[list[OrderingOption], str]:
        """STEP 4: 선택 옵션에 따른 특이사항 표기 및 AI 분석 (Gemini 활용)"""
        options_summary = "\n".join(
            [f"- {o.option_type.name}: {o.recommended_qty}건" for o in options]
        )

        campaign_status = "캠페인 적용 중" if context.get("is_campaign") else "캠페인 없음"
        holiday_status = "휴일" if context.get("is_holiday") else "평일"
        prompt = create_ordering_reasoning_prompt(
            store_id=store_id,
            current_date=target_date,
            campaign_status=campaign_status,
            holiday_status=holiday_status,
            options_summary=options_summary,
        )

        try:
            ai_raw_response = self.gemini.call_gemini_text(prompt, response_type="json")
            ai_data = (
                ai_raw_response
                if isinstance(ai_raw_response, dict)
                else json.loads(ai_raw_response)
            )
            if not isinstance(ai_data, dict):
                raise ValueError("ordering reasoning response must be a dict")

            summary_insight = ai_data.get("analysis_summary", "")
            closing_msg = ai_data.get("closing_message", "")

            for opt in options:
                for detail in ai_data.get("option_details", []):
                    if detail.get("option_type") == opt.option_type.name:
                        opt.reasoning = (
                            f"[{detail.get('impact_factor')}] {detail.get('description')}"
                        )
                        break
                if not opt.reasoning:
                    opt.reasoning = f"{opt.option_type.value} 기반 추천 수량입니다."

            full_summary = "\n\n".join(
                part for part in (summary_insight, closing_msg) if part
            ).strip()
            if not full_summary:
                full_summary = "과거 주문 데이터를 바탕으로 3가지 주문 옵션을 제안합니다."
        except (ValueError, TypeError, json.JSONDecodeError, RuntimeError) as e:
            logger.error(f"LLM Reasoning failed: {e}")
            full_summary = (
                "과거 주문 데이터를 바탕으로 산출된 옵션입니다. 매장 상황을 고려하여 선택해 주세요."
            )
            for opt in options:
                opt.reasoning = f"{opt.option_type.value} 데이터 기반"

        return options, full_summary

    def get_deadline_alerts(
        self, store_id: str, deadline_hour: int = 14, deadline_minute: int = 0
    ) -> DeadlineAlertResponse:
        """주문 마감까지 남은 시간 계산 및 20분 이내 알림 반환."""
        try:
            if _HAS_PYTZ:
                KST = _pytz.timezone("Asia/Seoul")
                now_kst = datetime.now(KST)
            else:
                now_kst = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))
        except (ValueError, TypeError, AttributeError):
            now_kst = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))

        deadline_today = now_kst.replace(
            hour=deadline_hour, minute=deadline_minute, second=0, microsecond=0
        )
        delta_seconds = (deadline_today - now_kst).total_seconds()
        delta_minutes = int(delta_seconds / 60)

        deadline_str = f"{deadline_hour:02d}:{deadline_minute:02d}"

        if delta_minutes < 0:
            return DeadlineAlertResponse(
                store_id=store_id,
                deadline=deadline_str,
                minutes_remaining=0,
                alert_level="passed",
                message="오늘 주문 마감이 지났습니다.",
                should_alert=False,
                notification_id=2001,
                title="오늘 주문 마감이 지났습니다",
                deadline_minutes=0,
                target_path="/ordering",
                focus_option_id=None,
                target_roles=["store_owner"],
            )
        if delta_minutes <= 20:
            return DeadlineAlertResponse(
                store_id=store_id,
                deadline=deadline_str,
                minutes_remaining=delta_minutes,
                alert_level="urgent",
                message=f"주문 마감 {delta_minutes}분 전입니다. 서둘러 주문하세요!",
                should_alert=True,
                notification_id=2001,
                title=f"주문 마감 {delta_minutes}분 전입니다",
                deadline_minutes=delta_minutes,
                target_path="/ordering",
                focus_option_id="last_week",
                target_roles=["store_owner"],
            )
        return DeadlineAlertResponse(
            store_id=store_id,
            deadline=deadline_str,
            minutes_remaining=delta_minutes,
            alert_level="normal",
            message=f"주문 마감까지 {delta_minutes}분 남았습니다.",
            should_alert=False,
            notification_id=2001,
            title=f"주문 마감까지 {delta_minutes}분 남았습니다",
            deadline_minutes=delta_minutes,
            target_path="/ordering",
            focus_option_id="last_week",
            target_roles=["store_owner"],
        )

    def recommend_ordering(
        self, payload: OrderingRecommendationRequest
    ) -> OrderingRecommendationResponse:
        """API Endpoint 용 STEP 3 & 4 통합 실행"""
        logger.info(
            f"Processing Ordering Recommendation API for {payload.store_id} at {payload.target_date}"
        )
        current_context = payload.current_context or {}
        target_product = current_context.get("target_product")

        options = self.calculate_base_ordering_options(
            payload.store_id, payload.target_date, target_product
        )
        self._append_special_event_option_if_needed(
            payload.store_id, payload.target_date, target_product, current_context, options
        )

        enriched_options, summary_insight = self.generate_ordering_insights_and_questions(
            payload.store_id, payload.target_date, current_context, options
        )

        if not summary_insight:
            summary_insight = "과거 주문 패턴과 시즌성 가중치를 반영한 주문 추천입니다."

        self._enrich_option_contract_fields(enriched_options, current_context)
        deadline_hour, deadline_minute = self._resolve_deadline(
            payload.target_date, current_context
        )
        deadline_info = self.get_deadline_alerts(
            payload.store_id,
            deadline_hour=deadline_hour,
            deadline_minute=deadline_minute,
        )

        return OrderingRecommendationResponse(
            store_id=payload.store_id,
            recommendations=enriched_options,
            summary_insight=summary_insight,
            deadline_minutes=deadline_info.minutes_remaining,
            deadline_at=deadline_info.deadline,
            purpose_text=self._build_purpose_text(bool(current_context.get("notification_entry"))),
            caution_text=self._build_caution_text(),
            weather_summary=self._extract_weather_summary(current_context),
            trend_summary=self._extract_trend_summary(current_context, enriched_options),
            business_date=payload.target_date,
        )
