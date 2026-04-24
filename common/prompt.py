PRODUCTION_ALARM_PROMPT_TEMPLATE = """
        당신은 매장 생산 관리 전문가입니다. 다음 데이터를 바탕으로 점주가 이해하기 쉬운 짧고 명확한 알림 메시지를 생성하세요.

        - 제품명: {sku}
        - 현재고: {current_stock}
        - 1시간 뒤 예상 재고: {predicted_stock:.1f}
        - 위험 여부: {risk_status}
        - 예상 품절 시점: {stockout_at}
        - 4주 평균 생산 패턴: {pattern_4w}

        알림 메시지 조건:
        - 현재고와 예상 재고를 포함할 것.
        - 생산이 필요한 경우 시점과 수량을 제안할 것.
        - 2-3문장 이내로 친절하게 작성할 것.
        """

ORDERING_REASONING_PROMPT_TEMPLATE = """
        당신은 매장 주문 관리 전문가입니다. 다음 데이터를 바탕으로 점주가 주문 옵션을 선택할 수 있도록 상세한 추천 근거를 생성하세요.

        - 매장 ID: {store_id}
        - 현재 날짜: {current_date}
        - 캠페인 여부: {campaign_status}
        - 공휴일/시즌 여부: {holiday_status}
        - 최근 운영 컨텍스트: {context_summary}

        추천 옵션:
        {options_summary}

        추천 근거 생성 조건:
        - 옵션별 지표(판매 추세, 재고 커버리지, 유통기한 위험도)를 반드시 근거에 반영할 것.
        - 캠페인이나 공휴일 신호가 있는 경우 이를 반영하여 특정 옵션을 더 강조할 것.
        - 점주가 의사결정을 내릴 수 있도록 각 옵션의 장단점을 짧게 언급할 것.
        - 자연스러운 문장으로 작성할 것.
        - 반드시 JSON으로 반환하고, option_details의 option_type은 LAST_WEEK/TWO_WEEKS_AGO/LAST_MONTH 중 하나를 사용하세요.
        """


def create_production_alarm_prompt(
    sku: str,
    current_stock: int,
    predicted_stock: float,
    risk_status: str,
    stockout_at: str,
    pattern_4w: list,
) -> str:
    """생산 알람 프롬프트 생성"""
    return PRODUCTION_ALARM_PROMPT_TEMPLATE.format(
        sku=sku,
        current_stock=current_stock,
        predicted_stock=predicted_stock,
        risk_status=risk_status,
        stockout_at=stockout_at,
        pattern_4w=pattern_4w,
    )


def create_ordering_reasoning_prompt(
    store_id: str,
    current_date: str,
    campaign_status: str,
    holiday_status: str,
    options_summary: str,
    context_summary: str,
) -> str:
    """주문 추천 근거 설명 프롬프트 생성"""
    return ORDERING_REASONING_PROMPT_TEMPLATE.format(
        store_id=store_id,
        current_date=current_date,
        campaign_status=campaign_status,
        holiday_status=holiday_status,
        options_summary=options_summary,
        context_summary=context_summary,
    )
