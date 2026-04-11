# br-korea-poc-ai

BR Korea 매장 운영 지원 POC의 AI 서비스입니다. FastAPI 기반으로 실행되며, Google Gemini를 활용한 매출 분석, 생산/주문 가이드, 지식 검색(RAG) 기능을 제공합니다.

## 개요

한국 프랜차이즈 도넛/베이커리 매장(POC 대상)의 점주가 자연어로 질문하면 AI가 DB에서 실제 판매 데이터를 조회·분석해 인사이트와 실행 가능한 액션을 반환합니다. 백엔드 서비스(`br-korea-poc-backend`)와 독립적으로 운영되는 별도 서버입니다.

주요 기능:

- 매출 자연어 질의 (채널·결제수단·기간 비교 포함)
- 생산 알람 및 시뮬레이션 리포트 생성
- 주문 추천 (전주/전전주/전월 기준 3가지 옵션)
- pgvector 기반 벡터 DB 지식 검색 및 QA 캐시
- AI 응답 품질 평가 (LLM-as-a-Judge)
- 시계열 판매량 예측 ML 모델 (LightGBM / RandomForest fallback)

## Tech Stack

| 구분 | 라이브러리 | 버전 |
|---|---|---|
| 웹 프레임워크 | FastAPI | 0.115.0 |
| ASGI 서버 | uvicorn[standard] | 0.30.6 |
| 데이터 검증 | pydantic | 2.9.2 |
| 설정 관리 | pydantic-settings | 2.5.2 |
| AI 모델 | google-generativeai (Gemini 2.5 Flash) | — |
| 임베딩 | gemini-embedding-001 (3072차원) | — |
| 벡터 DB | pgvector + SQLAlchemy | — |
| DB 드라이버 | psycopg2-binary | — |
| 데이터 분석 | pandas, numpy | — |
| ML | scikit-learn (fallback), LightGBM (선택) | — |
| 벡터 검색 | faiss-cpu | — |
| 테스트 | pytest | 8.3.3 |

## Directory Structure

```text
br-korea-poc-ai/
├── run.py                      # uvicorn 서버 실행 엔트리
├── build_knowledge_base.py     # pgvector 지식 베이스 초기화/임베딩 스크립트
├── generate_insights.py        # 독립 인사이트 생성 스크립트
├── requirements.txt
├── environment.yml
├── Dockerfile
├── api/                        # FastAPI 앱
│   ├── main.py                 # 앱 초기화, 라우터 등록, lifespan 훅
│   ├── config.py               # 환경 변수 설정 (Settings, port: 8001)
│   ├── dependencies.py         # 서비스 DI 팩토리, Bearer 토큰 검증
│   ├── schemas.py              # API 전용 Pydantic 모델
│   └── routers/
│       ├── generation.py       # POST /generation — 파이프라인 실행
│       ├── sales.py            # POST /sales/query, /sales/query/channel-payment
│       └── management.py       # POST /production/simulation, /ordering/recommend (미완성)
├── common/                     # 공통 유틸리티
│   ├── gemini.py               # Gemini 클라이언트 (텍스트/이미지/임베딩, CSV 과금 로깅)
│   ├── logger.py               # 구조화 로깅 및 timefn 데코레이터
│   └── prompt.py               # 프롬프트 템플릿 함수
├── services/                   # 핵심 비즈니스 로직
│   ├── orchestrator.py         # 에이전트 오케스트레이터 (의도 분류 → RAG → 도메인 에이전트)
│   ├── sales_analyzer.py       # 매출 분석 에이전트 (시맨틱 캐시, 가드레일, Gemini 호출)
│   ├── sales_analysis_engine.py# PostgreSQL 직접 조회 엔진 (채널믹스, 수익성, 교차판매 등)
│   ├── rag_service.py          # pgvector 벡터 검색 + QA 캐시 + Excel 데이터 RAG
│   ├── semantic_layer.py       # 자연어 → 비즈니스 KPI 매핑
│   ├── predictor.py            # InventoryPredictor (GBDT 기반 시계열 판매량 예측)
│   ├── production_service.py   # 생산 알람 및 시뮬레이션 리포트
│   ├── production_agent.py     # 생산 관리 에이전트 보조 로직
│   ├── ordering_service.py     # 주문 추천 (3가지 옵션, 과거 데이터 기반)
│   ├── inventory_engine.py     # 재고 계산 엔진
│   ├── generator.py            # 생성 응답 보조 로직
│   ├── data_loader.py          # 분석용 데이터 로더
│   └── weather_service.py      # 외부 조건(날씨) 보조 서비스
├── pipeline/
│   ├── run.py                  # 파이프라인 진입점 (프롬프트 → AgentOrchestrator)
│   └── train_model.py          # InventoryPredictor 배치 학습 스크립트
├── schemas/
│   └── contracts.py            # 도메인 계약 모델 (생산·주문·매출 Pydantic 스키마)
├── evaluators/
│   └── basic.py                # QualityEvaluator (LLM-as-a-Judge, 신뢰도 0~1 점수)
├── tests/
│   ├── conftest.py
│   ├── test_api_integration.py
│   ├── test_ai_agents.py
│   └── test_pipeline.py
└── eval-data/
    └── sample.json
```

## 환경 변수

프로젝트 루트에 `.env` 파일을 생성합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `API_KEY` | (필수) | Gemini API 키 |
| `AI_SERVICE_TOKEN` | (빈 값) | Bearer 토큰 (미설정 시 로컬 개발 모드, 검증 생략) |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5435/br_korea_poc` | PostgreSQL 연결 |
| `APP_ENV` | `local` | 실행 환경 |
| `APP_HOST` | `0.0.0.0` | 바인딩 호스트 |
| `APP_PORT` | `8001` | 개발 서버 포트 |

## 실행 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt

# LightGBM 사용 시 별도 설치
pip install lightgbm
```

### 2. 서버 실행

```bash
python run.py
```

`APP_ENV=local`이면 uvicorn reload가 자동 활성화됩니다. 기본 포트는 **8001**입니다.

- Swagger UI: `http://localhost:8001/docs`
- ReDoc: `http://localhost:8001/redoc`

### 3. 지식 베이스 초기화 (최초 1회)

매장 정보(`STOR_MST.xlsx`), 캠페인 마스터(`CPI_MST.xlsx`), 결제 코드(`PAY_CD.csv`)를 pgvector DB에 임베딩합니다.

```bash
python build_knowledge_base.py
```

### 4. ML 모델 학습 (선택)

```bash
python pipeline/train_model.py
```

### 5. 테스트 실행

```bash
pytest tests/
```

## 주요 API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 서비스 헬스체크 |
| POST | `/generation` | 자연어 프롬프트 기반 파이프라인 실행 |
| POST | `/sales/query` | 매출 자연어 질의 (종합 분석) |
| POST | `/sales/query/channel-payment` | 채널·결제수단 특화 분석 |
| POST | `/production/simulation` | 생산 가이드 시뮬레이션 리포트 |
| POST | `/ordering/recommend` | 주문 추천 (3가지 옵션 반환) |

`AI_SERVICE_TOKEN` 설정 시 모든 엔드포인트에 `Authorization: Bearer <token>` 헤더가 필요합니다.

## 서비스 구조 흐름

```
클라이언트 요청
    └── FastAPI 라우터
            └── AgentOrchestrator
                    ├── SemanticLayer      (비즈니스 KPI 매핑)
                    ├── QueryClassifier    (의도 분류 / 민감 정보 가드레일)
                    ├── RAGService         (pgvector 벡터 검색 + QA 캐시)
                    ├── SalesAnalyzer      (매출 분석 + DB 직접 조회)
                    │       └── SalesAnalysisEngine  (채널믹스, 수익성, 교차판매)
                    ├── ProductionService  (생산 알람, 시뮬레이션)
                    ├── OrderingService    (주문 추천)
                    └── QualityEvaluator   (응답 신뢰도 평가)
```

## 현재 상태 메모

- `api/routers/management.py`는 `router = APIRouter(...)` 선언이 누락된 미완성 상태이며 현재 실제 요청을 처리하지 않습니다.
- `SalesAnalysisEngine`은 PostgreSQL에 직접 연결해 실제 데이터를 조회합니다. DB 연결 실패 시 하드코딩된 fallback 값을 반환합니다.
- `InventoryPredictor`의 학습 데이터 경로(`resources/04_poc_data/`)는 실제 환경에 맞게 조정이 필요합니다.
- Gemini API 호출 내역은 `results/billing.csv`에 자동 기록됩니다.