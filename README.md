# br-korea-poc-ai

BR Korea 매장 운영 지원 POC의 AI 서비스입니다. FastAPI 기반으로 실행되며, Google Gemini를 활용한 매출 분석, 생산/주문 가이드, 지식 검색(RAG) 기능을 제공합니다. 현재 백엔드가 프론트 계약을 기준으로 AI 응답을 어댑팅합니다.

## 개요

한국 프랜차이즈 도넛/베이커리 매장(POC 대상)의 점주가 자연어로 질문하면 AI가 DB에서 실제 판매 데이터를 조회·분석해 인사이트와 실행 가능한 액션을 반환합니다. 백엔드 서비스(`br-korea-poc-backend`)와 독립적으로 운영되는 별도 서버입니다.

주요 기능:

- 매출 자연어 질의 (채널·결제수단·기간 비교·교차판매 분석 포함)
- 생산 알람 및 시뮬레이션 리포트 생성, 찬스로스 정량 추정
- 주문 추천 (전주/전전주/전월 기준 3가지 옵션 + 캠페인·요일 시즌성 가중치)
- pgvector 기반 벡터 DB 지식 검색 및 시맨틱 QA 캐시
- AI 응답 품질 평가 (LLM-as-a-Judge)
- 시계열 판매량 예측 ML 모델 (LightGBM / RandomForest fallback)
- 잔차 표준편차 기반 예측 신뢰구간 산출
- 연관규칙(Support/Confidence/Lift) 기반 교차판매 조합 분석

## Tech Stack

| 구분 | 라이브러리 | 버전 |
|---|---|---|
| 웹 프레임워크 | FastAPI | 0.115.0 |
| ASGI 서버 | uvicorn[standard] | 0.30.6 |
| 데이터 검증 | pydantic | 2.9.2 |
| 설정 관리 | pydantic-settings | 2.5.2 |
| AI 모델 | google-genai (Gemini 3 Flash Preview) | — |
| 임베딩 | gemini-embedding-001 (3072차원) | — |
| 벡터 DB | pgvector + SQLAlchemy | — |
| DB 드라이버 | psycopg2-binary | — |
| 데이터 분석 | pandas, numpy | — |
| ML | scikit-learn (fallback), LightGBM | — |
| 모델 직렬화 | joblib | — |
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
│   ├── config.py               # 환경 변수 설정 (Settings, default port: 8001)
│   ├── dependencies.py         # 서비스 DI 팩토리, Bearer 토큰 검증
│   ├── schemas.py              # API 전용 Pydantic 모델
│   └── routers/
│       ├── generation.py       # POST /generation — 파이프라인 실행
│       ├── sales.py            # POST /sales/query, /sales/query/channel-payment
│       └── management.py       # POST /api/production/simulation, /ordering/recommend, /management/* legacy aliases
├── common/                     # 공통 유틸리티
│   ├── gemini.py               # Gemini 클라이언트 (텍스트/이미지/임베딩, CSV 과금 로깅)
│   ├── logger.py               # 구조화 로깅 및 timefn 데코레이터
│   ├── llm_logger.py           # LLM 호출 감사 로그 (전화번호/이메일/주민번호 마스킹)
│   ├── rate_limiter.py         # 슬라이딩 윈도우 Rate Limiter (분당 60회)
│   ├── evaluator.py            # QualityEvaluator (LLM-as-a-Judge, 신뢰도 0~1 점수)
│   └── prompt.py               # 프롬프트 템플릿 함수
├── services/                   # 핵심 비즈니스 로직
│   ├── orchestrator.py         # 에이전트 오케스트레이터 (의도 분류 → RAG → 도메인 에이전트)
│   ├── sales_analyzer.py       # 매출 분석 에이전트 (시맨틱 캐시, 가드레일, Gemini 호출)
│   ├── sales_agent.py          # PostgreSQL 직접 조회 엔진 (채널믹스, 수익성, 교차판매 Lift 포함)
│   ├── query_classifier.py     # 규칙 기반 질의 분류기 (SENSITIVE/CHANNEL/COMPARISON/...)
│   ├── query_routing.py        # 질의 유형 기반 라우팅 보조 로직
│   ├── channel_payment_analyzer.py # 채널·결제수단 특화 분석 에이전트
│   ├── grounded_analyzer.py    # Text-to-SQL 파이프라인 기반 근거 데이터 포함 응답 생성
│   ├── sql_pipeline.py         # SQLGenerator + QueryExecutor (스키마 레지스트리 포함)
│   ├── chance_loss_engine.py   # 찬스로스 정량 추정 엔진 (매출 0구간 탐지 + 인접 평균 손실 추정)
│   ├── seasonality_engine.py   # 시즌성 가중치 엔진 (캠페인 1순위, 요일별 역사 가중치 2순위)
│   ├── rag_service.py          # pgvector 벡터 검색 + QA 캐시 + Excel 데이터 RAG
│   ├── semantic_layer.py       # 자연어 → 비즈니스 KPI 매핑
│   ├── inventory_predictor.py  # InventoryPredictor (LightGBM 기반 시계열 예측)
│   ├── inventory_reversal_engine.py # 재고 역산 엔진 (기초재고+생산-매출, 5분 단위 추정)
│   ├── production_service.py   # 생산 알람 및 시뮬레이션 리포트 (찬스로스 감소 효과 포함)
│   ├── production_agent.py     # 생산 관리 에이전트 보조 로직
│   ├── ordering_service.py     # 주문 추천 (3가지 옵션, 시즌성 가중치 적용)
│   ├── dashboard_service.py    # 홈 대시보드 집계 서비스
│   └── data_extraction_engine.py # intent 분류(7종) + SQL/API 우선 데이터 추출 엔진
├── pipeline/
│   ├── run.py                  # 파이프라인 진입점 (프롬프트 → AgentOrchestrator)
│   └── train_model.py          # InventoryPredictor 배치 학습 스크립트
├── schemas/
│   └── contracts.py            # 도메인 계약 모델 (생산·주문·매출 Pydantic 스키마)
├── tests/
│   ├── conftest.py
│   ├── test_api_integration.py
│   ├── test_ai_agents.py
│   ├── test_pipeline.py
│   └── test_quality_scenarios.py # 16개 품질 시나리오 테스트
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
| `APP_PORT` | `8001` | 개발 서버 기본 포트 |

- 소진공 빅데이터 OpenAPI(`certKey`)는 AI 서비스가 아닌 백엔드(`br-korea-poc-backend/.env`)에서 관리합니다.
- 상권 인텔리전스 외부 API(소진공 상권 경쟁사 조회) 호출은 백엔드가 담당하며, AI 서비스는 `EXTERNAL_API_KEY`/`SBIZ_API_COMMERCIAL_MAP_KEY`/`SBIZ_API_STORE_STATUS_KEY`를 직접 사용하지 않습니다.
- 상권·고객 분석 화면의 `store_reports`(소진공 API 키 상태/연동상태)는 백엔드 `market-intelligence` 응답 필드이며, AI 서비스는 해당 상태를 계산하거나 저장하지 않습니다.
- 주간 분석 리포트 다운로드(`GET /api/analytics/market-intelligence/weekly-report`)도 백엔드에서 markdown을 생성하며 AI 서비스는 파일 생성을 직접 담당하지 않습니다.
- `slsIdex` 실호출 기반 `실호출 미확인/점검 필요` 판정 또한 백엔드에서 처리하며, AI 서비스는 해당 외부 API 호출을 수행하지 않습니다.

## 실행 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 서버 실행

```bash
python run.py
```

`APP_ENV=local`이면 uvicorn reload가 자동 활성화됩니다. `run.py` 단독 실행 기본 포트는 **8001**이고, 루트 `docker-compose.yml`에서는 **6001**로 노출합니다.

- 단독 실행 Swagger UI: `http://localhost:8001/docs`
- 단독 실행 ReDoc: `http://localhost:8001/redoc`

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

## 코드 컨벤션 (ruff / black / mypy)

```bash
# Lint
ruff check .

# Format
black .

# Type check
mypy --explicit-package-bases api common services pipeline schemas
```

`mypy`는 `pyproject.toml` 설정에 따라 `tests/`, `scripts/` 디렉터리를 제외하고 검사합니다.

## 주요 API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 서비스 헬스체크 |
| POST | `/generation` | 자연어 프롬프트 기반 파이프라인 실행 |
| POST | `/sales/query` | 매출 자연어 질의 (종합 분석) |
| POST | `/sales/query/channel-payment` | 채널·결제수단 특화 분석 |
| POST | `/api/production/simulation` | 생산 가이드 시뮬레이션 리포트 |
| POST | `/ordering/recommend` | 계약 기반 주문 추천 |

레거시 호환용 엔드포인트도 함께 유지합니다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/management/production/predict` | 백엔드 호환용 생산 예측 |
| POST | `/management/ordering/recommend` | 백엔드 호환용 주문 추천 |

`AI_SERVICE_TOKEN` 설정 시 모든 엔드포인트에 `Authorization: Bearer <token>` 헤더가 필요합니다.

## 계약 스키마

- 생산 시뮬레이션 요청/응답 계약은 [`schemas/contracts.py`](/Users/hanna/Documents/br-korea-poc/br-korea-poc-ai/schemas/contracts.py:1)의 `SimulationRequest`, `SimulationReportResponse`를 기준으로 관리합니다.
- 백엔드가 AI 서비스를 프록시하거나 매핑할 때는 위 계약과 정합성을 유지해야 합니다.

## Backend 연동 메모

백엔드는 프론트 계약을 유지하기 위해 AI 요청/응답을 어댑팅합니다.

### Sales Query 입력 계약

- AI 원본 계약:
  - `POST /sales/query`
  - body: `{"store_id": "...", "query": "..."}`
- 백엔드는 프론트의 `prompt`를 받아 위 형태로 변환해 호출합니다.

### Sales Query 응답 계약

- AI 원본 응답은 `answer`, `source_data_period`, `channel_analysis`, `profit_simulation` 중심입니다.
- 백엔드는 이를 프론트 표시용 `text`, `evidence`, `actions`, `visual_data` 중심 구조로 변환합니다.
- AI 서비스 자체는 도메인 분석 계약을 유지하고, 프론트 고정 계약은 백엔드에서 보장합니다.

## 서비스 구조 흐름

```
클라이언트 요청
    └── FastAPI 라우터
            └── AgentOrchestrator
                    ├── SemanticLayer          (비즈니스 KPI 매핑)
                    ├── QueryClassifier        (의도 분류: SENSITIVE/CHANNEL/COMPARISON/... 가드레일)
                    ├── RAGService             (pgvector 벡터 검색 + 시맨틱 QA 캐시)
                    ├── QualityEvaluator       (응답 신뢰도 평가, LLM-as-a-Judge)
                    ├── SalesAnalyzer          (매출 분석 + DB 직접 조회)
                    │       ├── SalesAnalysisAgent
                    │       │       ├── analyze_real_channel_mix()      (채널믹스)
                    │       │       ├── simulate_real_profitability()   (수익성)
                    │       │       ├── extract_cross_sell_combinations() (교차판매 Lift)
                    │       │       ├── calculate_comparison_metrics()  (L4W vs P4W)
                    │       │       └── extract_store_profile()         (Top Items, 피크 시간, 음료 비중)
                    │       └── ChannelPaymentAnalyzer (채널/결제 특화)
                    ├── ProductionService      (생산 알람, 시뮬레이션, 찬스로스 감소 효과)
                    │       └── ChanceLossEngine (매출 0구간 기반 기회손실 정량 추정)
                    ├── OrderingService        (주문 추천)
                    │       └── SeasonalityEngine (캠페인 + 요일별 역사 가중치)
                    ├── InventoryPredictor     (LightGBM 기반 시계열 예측 + 캠페인 승수 보정)
                    └── InventoryReversalEngine (기초재고+생산-매출 역산, 5분 단위 추정)
```

## ML / 분석 모델 상세

### InventoryPredictor (`services/inventory_predictor.py`)
- **모델**: LightGBM GBDT (`objective=regression`, `metric=mae`)
- **피처**: `hour`, `weekday`, `is_weekend`, `lag_1h`, `lag_2h`, `rolling_mean_3h`, `store_avg`, `item_avg`
- **학습**: 판매 0 데이터를 비율 1.5:1로 다운샘플, 상위 1% 이상치 제거, 판매량 기반 샘플 가중치 적용
- **추론**: ML 예측(70%) + 실시간 판매 속도(30%) 하이브리드 보정
- **신뢰구간**: 과거 잔차 표준편차(±1σ) 기반, 데이터 부족 시 비율 기반 fallback

### SeasonalityEngine (`services/seasonality_engine.py`)
- **1순위**: 캠페인 마스터 날짜 범위 조회 → 캠페인 가중치 적용
- **2순위**: 역사적 판매 데이터 기반 요일별 상대 가중치 (일별 평균 / 전체 평균)
- **3순위**: 기본값 1.0

### ChanceLossEngine (`services/chance_loss_engine.py`)
- 영업 시간(8~22시) 중 매출 0 구간 탐지
- 인접 ±2시간 평균으로 손실 수량 추정
- 데이터 커버리지와 생산 기록 유무 기반 신뢰도(`high` / `medium` / `low`) 산출

### 교차판매 연관규칙 (`SalesAnalysisAgent.extract_cross_sell_combinations`)
- 동일 영수증 내 아이템 쌍을 DB CTE 쿼리로 집계
- **Support**: 전체 영수증 중 두 상품 동반 등장 비율
- **Confidence**: item_a 구매 시 item_b 구매 확률
- **Lift**: 독립 구매 대비 동반 구매 상승 배율 (>1이면 시너지)

## 현재 상태 메모

- `SalesAnalysisAgent`은 PostgreSQL에 직접 연결해 실제 데이터를 조회합니다. DB 연결 실패 시 하드코딩된 fallback 값을 반환합니다.
- `InventoryPredictor`의 학습 데이터 경로(`resources/04_poc_data/`)는 실제 환경에 맞게 조정이 필요합니다.
- 사용자 피드백 반영 온라인 학습 루프는 미구현 상태입니다 (P2).
- Gemini API 호출 내역은 `results/billing.csv`에 자동 기록됩니다.

- 백엔드가 실호출 상태를 판정하는 대상 API(`sns/hotplace/delivery/tour/stor/sls`) 목록 변경 시 AI 서비스 코드는 수정하지 않습니다.

## Session Update (2026-04-20)

- 이번 세션에서 AI 서비스 코드 변경은 없으며, 주간 상권 리포트 다운로드 안정화는 backend 영역에서 처리되었습니다.
- 메뉴 이미지 URL 서빙/표시 작업은 backend + frontend 영역에서 처리되었고 AI 서비스 코드는 변경하지 않았습니다.
- 이미지 미존재 기본 썸네일(placeholder) 적용 역시 frontend 정적 에셋 처리이며 AI 서비스 변경 사항은 없습니다.
- CORS/500 안정화(`notifications`, `home`, `production`) 작업도 backend+frontend 영역에서 처리되었고 AI 서비스 코드는 변경하지 않았습니다.
- 주문관리/발주이력 경계 분리 및 발주이력 인사이트 고도화 작업 역시 backend+frontend 영역 구현이며 AI 서비스 코드는 변경하지 않았습니다.
- 상권/고객 분석 실데이터 강제(합성값 제거, 연도/분기 미존재 시 실데이터 폴백) 작업도 backend 영역 구현이며 AI 서비스 코드는 변경하지 않았습니다.
- 상권/고객 분석 5개 블록(업종/매출/인구/지역/고객특성) 화면 재구성 및 응답 스키마 확장 작업 역시 backend+frontend 범위이며 AI 서비스 코드는 변경하지 않았습니다.
- 신규/단골 비율 고객식별 컬럼 자동탐지 템플릿 확장도 backend 영역 구현이며 AI 서비스 코드는 변경하지 않았습니다.
