# br-korea-poc-ai

> **전역 시스템 제약조건 및 코드 컨벤션**
> 본 프로젝트는 엔터프라이즈 B2B SaaS 아키텍처를 지향하며, 공통 코드 컨벤션을 따릅니다.
> 주요 AI 제약: **모듈형 구조, 파이프라인 중심 실행, 예측(ML/DL)과 생성(GenAI)의 분리, 검증 가능한 출력**

br-korea-poc의 지능형 매장 운영 지원 AI 에이전트 서비스입니다.

## Architecture

본 서비스는 **Predictive AI(ML/DL)**와 **Generative AI(Gemini 3.0 Flash)**를 결합한 하이브리드 구조를 가집니다.

- **Predictor Layer (ML/DL)**: 시계열 데이터(재고, 판매) 예측 및 자연어 질의 분류(Intent Classification) 수행.
- **Agent Layer**: 각 도메인별(생산, 주문, 매출 분석) 비즈니스 로직 처리.
- **Orchestrator Layer**: 사용자 질의 라우팅, RAG 검색 연동, 보안 가드레일 적용.
- **RAG Layer**: 운영 가이드 및 FAQ 기반의 근거 있는 답변 및 출처 제공.

## Summary

- **생산 관리 Agent**: 1시간 후 예상 재고를 ML로 예측하고, Gemini를 통해 점주 맞춤형 생산 알림 메시지 생성.
- **주문 관리 Agent**: 과거 데이터를 기반으로 3가지 주문 옵션을 ML로 산출하고, Gemini로 추천 근거(이벤트, 시즌 등) 생성.
- **매출 분석 Agent**: 자연어 질의를 분석하여 실시간 데이터 기반 통찰력과 실행 가능한 액션 아이템 제안.
- **보안 가드레일**: 민감 정보(원가, 수익 등) 식별 및 차단 알고리즘 내장.

## Directory Structure

```text
api/                # FastAPI 엔드포인트 및 스키마
├── routers/        # 도메인별 API 라우터 (sales, management, generation)
├── dependencies.py # 의사결정 및 서비스 주입(DI)
└── schemas.py      # API 입출력 Pydantic 모델
common/             # 공통 유틸리티
├── gemini.py       # Gemini 3.0 Flash 클라이언트 설정
├── logger.py       # 표준 구조화 로깅 시스템
├── prompt.py       # 프롬프트 템플릿 및 팩토리
└── config.py       # 시스템 설정 및 상수
services/           # 핵심 비즈니스 로직
├── predictor.py    # ML/DL 기반 예측 및 분류 모델
├── orchestrator.py # 에이전트 오케스트레이션 및 라우팅
├── rag_service.py  # RAG 기반 지식 검색 및 응답
├── production_service.py # 생산 관리 로직
├── ordering_service.py   # 주문 관리 로직
└── sales_analyzer.py     # 매출 분석 로직
pipeline/           # 전체 워크플로우 실행 엔진
└── run.py          # 파이프라인 진입점
```

## Code Conventions

- **예측과 생성의 분리**: 모든 수치 예측 및 분류 로직은 `services/predictor.py`에 구현하며, 생성 로직은 Gemini를 호출합니다.
- **의존성 주입 (DI)**: FastAPI의 `Depends`를 사용하여 서비스 및 클라이언트를 주입하며, 싱글톤 패턴을 지향합니다.
- **프롬프트 관리**: 모든 프롬프트는 `common/prompt.py`에서 템플릿화하여 관리하며, 하드코딩을 지양합니다.
- **비동기 처리**: 외부 API(Gemini) 호출 시 `asyncio.to_thread` 또는 `async` 함수를 활용하여 이벤트 루프 블로킹을 방지합니다.
- **로깅 표준**: 모든 서비스는 `common/logger.py`에서 초기화된 로거를 사용하여 `info`, `warning`, `error` 레벨을 준수합니다.
- **응답 일관성**: 모든 생성 응답은 `text`, `evidence`, `actions` 구조를 포함하도록 구성합니다.

## Review Policy

- 생산과 주문 추천은 의사결정 보조로 한정하고 최종 승인 주체는 점주로 유지한다.
- 매출, 손익, 점포 성과, 생산량, 원가 정보는 민감정보로 분류하고 LLM 전송 전 필터링한다.
- 모든 추천과 분석 응답은 감사 가능한 로그(billing.csv 및 logger)로 남긴다.

## Environment & Run

- `.env`에 `API_KEY` 및 `PROJECT_ID` 설정 필수.
- `python run.py`로 서버 기동 (local 시 reload 활성화).
