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
        당신은 매장 주문 관리 전문가입니다. 점주가 발주 추천 화면에서 3가지 옵션 중 하나를 고를 수 있도록, 옵션별로 차별화된 추천 근거와 선택 가이드를 한 번에 생성하세요.

        - 매장 ID: {store_id}
        - 현재 날짜: {current_date}
        - 캠페인 여부: {campaign_status}
        - 공휴일/시즌 여부: {holiday_status}
        - 최근 운영 컨텍스트: {context_summary}

        세 가지 옵션의 의미:
        - LAST_WEEK(전주, 같은 요일): 가장 최근성을 반영. 최근 운영 흐름이 안정적이라 그대로 이어가고 싶을 때 적합.
        - TWO_WEEKS_AGO(전전주, 같은 요일): 행사·이벤트 영향이 적은 안정 비교 기준. 최근 변동성을 걸러서 보고 싶을 때 적합.
        - LAST_MONTH(지난달, 같은 요일): 시즌성과 채널 변동을 반영. 전월 패턴과 비교하면서 결정하고 싶을 때 적합.

        추천 옵션 데이터:
        {options_summary}

        생성 규칙:
        - 옵션마다 들어온 9종 지표(기준일, 보정 전 주문량, 추천 주문량, 품목 수, 최근 7일 판매량, 판매 추세, 재고 커버리지, 유통기한 고위험, 최종 보정계수)를 반드시 description에 인용한다. 수치는 들어온 값을 그대로 사용(예: "판매 추세 1.08x", "재고 커버리지 2.3일").
        - 각 description은 2~3문장으로 작성. 1번째 문장은 "어떤 데이터로 어떻게 보정해서 추천 주문량이 나왔는지" 계산 근거. 2~3번째 문장은 "언제 이 옵션을 선택하는 게 좋은지" 선택 가이드.
        - 캠페인/공휴일 신호가 있으면 가장 적합한 옵션을 description에서 자연스럽게 강조한다.
        - 점주 대상 친근체(예: "~했어요", "~할 때 적합합니다"). 마케팅 톤 금지.
        - Gemini는 단 한 번 호출되며, 응답에는 반드시 3개 옵션 모두에 대한 option_details가 포함되어야 한다.

        반드시 아래 JSON 스키마로만 응답:
        {{
          "analysis_summary": "3개 옵션을 한 줄로 비교 요약",
          "closing_message": "점주에게 보내는 마무리 한 줄(선택)",
          "option_details": [
            {{
              "option_type": "LAST_WEEK",
              "impact_factor": "최근성",
              "description": "2~3문장 (계산 근거 + 선택 가이드)"
            }},
            {{
              "option_type": "TWO_WEEKS_AGO",
              "impact_factor": "안정성",
              "description": "2~3문장 (계산 근거 + 선택 가이드)"
            }},
            {{
              "option_type": "LAST_MONTH",
              "impact_factor": "시즌성",
              "description": "2~3문장 (계산 근거 + 선택 가이드)"
            }}
          ]
        }}
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
