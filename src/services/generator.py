from __future__ import annotations

from src.schemas.contracts import BrandProfile, GenerationInput, GenerationOutput


class DraftGenerator:
    def run(self, payload: GenerationInput, brand: BrandProfile | None = None) -> GenerationOutput:
        tone = ", ".join(brand.brand_tone[:2]) if brand else "검수 가능한 의료 마케팅 톤"
        hospital_name = brand.hospital_name if brand else payload.product
        draft = (
            f"{hospital_name} MediFlow 초안\n\n"
            f"핵심 목표: {', '.join(payload.goals[:3])}\n"
            f"운영 톤: {tone}\n"
            f"대표 기능: {', '.join(payload.features[:4])}"
        )
        channels = {
            "blog": {
                "headline": f"{hospital_name} 브랜드 스토리와 이벤트 맥락 설명",
                "body": "시술 대상, 회복 과정, FAQ를 함께 담은 블로그형 초안",
                "cta": "상담 전 체크리스트 받기",
            },
            "sns": {
                "headline": "과장 없는 후킹 문구와 카드뉴스 구조",
                "body": "짧은 문장과 주의 문구를 분리한 SNS형 초안",
                "cta": "DM 상담 연결",
            },
            "web": {
                "headline": "랜딩 배너와 CTA 세트",
                "body": "가격, 대상 환자, 주의 문구를 포함한 웹형 초안",
                "cta": "상세 패키지 보기",
            },
            "app": {
                "headline": "앱 등록용 제목과 혜택 요약",
                "body": "플랫폼 규격에 맞춘 요약형 초안",
                "cta": "예약 가능 시간 확인",
            },
        }
        review_notes = [
            "사람 검수 후 배포",
            "정책/심의 기준 별도 확인",
            "핵심 feature validation 충족 여부 확인",
        ]
        return GenerationOutput(draft=draft, review_notes=review_notes, channels=channels)
