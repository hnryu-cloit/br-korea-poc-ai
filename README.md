# br-korea-poc-ai

> **전역 시스템 제약조건 및 코드 컨벤션**
> 본 프로젝트는 엔터프라이즈 B2B SaaS 아키텍처를 지향하며, 공통 코드 컨벤션을 따릅니다.
> 상세 기준은 상위 디자인 시스템 및 저장소 전역 컨벤션 문서를 우선 확인하세요.
> 주요 AI 제약: **모듈형 구조, 파이프라인 중심 실행, 검증 가능한 출력**

br-korea-poc의 초안 생성 및 검토용 AI 파이프라인 MVP 초안입니다.

## Summary

- AI 역할: 5분 단위로 생산 대상 품목 재고를 조회하고 현재 재고량을 화면과 AP…, 최근 판매 추세와 4주 평균 생산 패턴을 결합해 1시간 후 예상 재고량…, 재고 소진 1시간 전에 생산 필요 시점을 자동 감지하고 PUSH 알림을…, 알림에는 현재고, 1시간 후 추정 재고, 4주 평균 1차 및 2차 생산…
- 입력 데이터: 제품 요약, 목표, 기능 목록
- 출력 데이터: 초안 결과와 기본 평가 결과
- 검토 방식: 샘플 평가와 사람 검토를 함께 사용

## Review Policy

- 생산과 주문 추천은 의사결정 보조로 한정하고 최종 승인 주체는 점주로 유지한다.
- 매출, 손익, 점포 성과, 생산량, 원가 정보는 민감정보로 분류하고 퍼블릭 LLM 직접 전송을 제한한다.
- 역할 기반 접근 제어를 적용해 점주, 매장 운영 담당자, 본사 담당자의 조회 범위를 구분한다.
- 질의 라우팅, 프롬프트, 응답, 추천 사유, 사용자 선택 결과를 감사 가능한 로그로 남긴다.

## Structure

```text
api/
├── config.py
├── dependencies.py
├── main.py
├── routers/
└── schemas.py
pipeline/
services/
schemas/
evaluators/
tests/
run.py
```

## Conventions

- 입력 계약과 출력 계약을 분리합니다.
- 모델 호출은 서비스 계층에서 감쌉니다.
- 파이프라인 단계와 평가 기준을 함께 유지합니다.
- API 계층은 파이프라인 실행을 감싸는 얇은 진입점으로 유지합니다.
- 사람이 검토할 수 있는 결과 형식을 기본값으로 둡니다.

## Stack

- Python 3.10+
- Pydantic
- FastAPI
- Uvicorn

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

## Test

```bash
pytest
```

## Evaluation

- 샘플 입력 기준 결과 검증
- 실패 케이스 확인
- 품질 기준과 승인 기준 문서화

## Environment

- `.env.example` 제공
- `APP_NAME`, `APP_ENV`, `APP_HOST`, `APP_PORT`, `AI_SERVICE_TOKEN` 분리

## Review Points

- API/파이프라인/평가 구조가 README에 드러나는가
- 파이프라인과 서비스 계층이 구조와 대응되는가
- 실행과 테스트 방법이 누락되지 않았는가
