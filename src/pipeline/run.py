from src.schemas.contracts import GenerationInput
from src.services.generator import DraftGenerator


FEATURES = [
  "5분 단위로 생산 대상 품목 재고를 조회하고 현재 재고량을 화면과 AP…",
  "최근 판매 추세와 4주 평균 생산 패턴을 결합해 1시간 후 예상 재고량…",
  "재고 소진 1시간 전에 생산 필요 시점을 자동 감지하고 PUSH 알림을…",
  "알림에는 현재고, 1시간 후 추정 재고, 4주 평균 1차 및 2차 생산…",
  "생산 등록 이후 재고 소진 전후 상태에 따라 찬스 로스 감소 또는 발생…",
  "주문 마감 20분 전에 점주에게 PUSH 알림을 보낸다.",
  "알림 클릭 시 주문 추천 채팅 화면으로 진입한다.",
  "전주 동요일, 전전주 동요일, 전월 동요일 기준의 3개 주문 옵션을 자…"
]
SUMMARY = "이 POC는 매장 운영 효율화와 찬스 로스 감소를 목표로 생산 관리, 주문 관리, 매출 분석 기능을 하나의 운영 지원 경험으로 묶는다. / 자동 의사결정이 아니라 점주 의사결정을 보조하는 방식으로 설계하며, 데이터 기반 설명 가능성과 운영 안전성을 함께 검증한다."
GOALS = ["5분 단위 재고 조회와 1시간 후 재고 예측을 바탕으로 생산 필요 시점을 감지하고 푸시 알림까지 연결한다.", "주문 마감 20분 전 점주에게 3개 주문 옵션과 추천 근거를 제공해 주문 누락을 줄인다.", "자연어 질의 기반 매출 분석 응답과 실행 가능한 인사이트를 제공한다.", "질의 유형 분류, SQL/API 우선 처리, LLM 호출 최소화, 민감정보 보호, RAG 근거 응답 구조를 POC 수준에서 검증한다."]


def run_pipeline() -> dict[str, object]:
    generator = DraftGenerator()
    result = generator.run(
        GenerationInput(
            product='br-korea-poc',
            summary=SUMMARY,
            goals=GOALS,
            features=FEATURES,
        )
    )
    return result.model_dump()
