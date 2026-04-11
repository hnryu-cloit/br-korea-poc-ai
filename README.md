# br-korea-poc-ai

BR Korea 매장 운영 지원 POC의 별도 AI 서비스입니다. 현재 코드는 FastAPI 기반으로 실행되며, 생성 파이프라인 실행과 매출 분석용 AI 엔드포인트를 제공합니다.

## 현재 구현 범위

- 서비스 헬스체크
- 생성 파이프라인 실행 API
- 매출 질의 API
- 채널/결제 분석 질의 API
- Gemini 클라이언트 의존성 주입
- Bearer 토큰 기반 서비스 인증
- 오케스트레이터, RAG, 생산/주문/매출 분석 서비스
- 파이프라인/시나리오/API 테스트 코드

## 주요 엔드포인트

- `GET /health`
- `POST /generation`
- `POST /sales/query`
- `POST /sales/query/channel-payment`

## Directory Structure

```text
api/                        # FastAPI 엔드포인트 및 스키마
├── routers/                # 도메인별 API 라우터
│   ├── sales.py            # 매출 질의/채널·결제 분석
│   ├── management.py       # 관리용 생산/주문 API (정비 필요)
│   └── generation.py       # 생성 파이프라인 실행 API
├── config.py               # 환경 설정
├── dependencies.py         # 서비스/클라이언트 DI 및 토큰 검증
├── main.py                 # FastAPI 앱 엔트리
└── schemas.py              # API 입출력 Pydantic 모델

common/                     # 공통 유틸리티
├── gemini.py               # Gemini 클라이언트 설정
├── logger.py               # 구조화 로깅
└── prompt.py               # 프롬프트 템플릿

services/                   # 핵심 비즈니스 로직
├── orchestrator.py         # 에이전트 오케스트레이션 및 라우팅
├── rag_service.py          # RAG 기반 지식 검색 및 응답
├── sales_analyzer.py       # 매출 분석기
├── sales_analysis_engine.py# 매출 분석 보조 엔진
├── semantic_layer.py       # 세맨틱 지표 해석 레이어
├── predictor.py            # 예측/분류 로직
├── inventory_engine.py     # 재고 계산 엔진
├── production_service.py   # 생산 관리 로직
├── production_agent.py     # 생산 에이전트 보조 로직
├── ordering_service.py     # 주문 추천 로직
├── generator.py            # 생성 응답 보조 로직
├── data_loader.py          # 분석용 데이터 로더
└── weather_service.py      # 외부 조건 보조 서비스

pipeline/                   # 전체 워크플로우 실행 엔진
├── run.py                  # 파이프라인 진입점
└── train_model.py          # 모델 학습 보조 스크립트

schemas/                    # 계약 스키마
└── contracts.py            # 도메인 계약 모델

evaluators/                 # 결과 평가기
└── basic.py                # 기본 evaluator

tests/                      # API/파이프라인/시나리오 테스트
├── test_api_integration.py
├── test_ai_agents.py
└── test_pipeline.py

run.py                      # 로컬 서버 실행 엔트리
build_knowledge_base.py     # 지식 베이스 생성 스크립트
generate_insights.py        # 인사이트 생성 스크립트
```

## Tech Stack

- FastAPI
- Pydantic v2 / pydantic-settings
- Google Gemini SDK
- pandas / numpy
- scikit-learn
- SQLAlchemy / pgvector / psycopg2-binary

## 실행

```bash
python3 -m pip install -r requirements.txt
python run.py
```

- 로컬 환경에서는 `APP_ENV=local`일 때 reload가 활성화됩니다.
- `/docs`, `/redoc`에서 API 문서를 확인할 수 있습니다.

## 환경 변수 메모

- `AI_SERVICE_TOKEN`: 설정 시 Bearer 토큰 검증에 사용
- 기타 앱 설정은 `api/config.py`의 `Settings`를 따릅니다.

## 현재 상태 메모

- AI 서비스는 별도 서버로 유지되고 있습니다.
- `api/routers/management.py`는 현재 코드 기준으로 구조 정비가 더 필요한 상태입니다.
