# br-korea-poc-ai

BR Korea 매장 운영 지원 POC의 AI 서비스입니다. FastAPI 기반으로 실행되며, Google Gemini를 활용한 매출 분석, 생산/주문 가이드, 지식 검색(RAG) 기능을 제공합니다. 현재 백엔드가 프론트 계약을 기준으로 AI 응답을 어댑팅합니다.

## 최근 업데이트 (2026-04-24)

- backend `load` 단계 안정화 마이그레이션(`store_clusters` 컬럼 보강)이 반영되었습니다.
  - AI 서비스 코드/계약 변경은 없습니다.

- 프론트 주문관리 mock 제거/실데이터 표기 전환이 반영되었습니다. (AI 서비스 코드 변경 없음)

- 백엔드에 `resource/06. 유통기한 및 납품일/*.xlsx` 기반 raw 적재 테이블이 추가되었습니다.
  - 이번 세션에서 AI 서비스 라우터/스키마/서비스 코드는 변경하지 않았고, backend 데이터 자산 확장 사항만 문서 동기화했습니다.
- backend 주문 도메인에서 `options/history` 근거가 납품/유통기한 실데이터로 강화되었습니다.
  - 이번 세션에서도 AI 서비스 코드/계약 변경은 없습니다.
- backend 주문 응답 계약 보강(`deadline_items` 명시)과 스케줄 대표값 결정 로직 보강이 반영되었습니다.
  - 이번 세션에서도 AI 서비스 코드/계약 변경은 없습니다.

## 최근 업데이트 (2026-04-23)

- 백엔드 골든쿼리 자산(`../br-korea-poc-backend/docs/golden-queries-new.csv`)이 `일반화 쿼리`/`예시 쿼리` 분리 컬럼으로 정비되었습니다.
  - AI 서비스 코드/계약 변경은 없고, 운영 참조 자산 구조 동기화만 반영했습니다.

- `docs/design-docs.md`에 본사 시연자(`hq_admin`)·점주 실사용자(`store_owner`) 이중 타깃 관점이 반영되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없고, 문서 관점 정합만 반영했습니다.

- `docs/design-docs.md` 페이지/콘텐츠 전략 문서가 라우터 기준으로 정비되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없고, 문서 정합성 정비만 반영했습니다.

- 점주 골든쿼리 데이터셋 문서(`docs/golden-queries-store-owner.csv`)가 추가되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없으며, 데이터셋은 백엔드 DB 스키마 기반 질의 템플릿 자산입니다.
- 동일 문서에 점주 추가 질문 200건이 반영되어 총 400건으로 확장되었습니다.
  - AI 서비스 코드는 변경하지 않았고, 운영용 질의 자산 확장만 반영했습니다.
- 골든쿼리 자산은 `질문번호`를 `그룹번호-순번-` 형식(예: `067-003-`)으로 표기해 연결형 질문 흐름을 식별합니다.
- 골든쿼리 자산에 기준일시 `2026-03-05 09:00 (KST)`가 고정 반영되었고, 쿼리/예상답변도 동일 기준으로 표기됩니다.

- 프론트 사이드바 상단 `AgentGo Biz` 로고 클릭 동선이 대문(`/`)으로 변경되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 `/settings` 화면 셸이 `Settings v3` 원본 HTML 기준으로 정렬되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 `/settings` 내부 패널/모달이 `Settings v3` 원본 마크업 기준으로 재작성되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 settings 코드가 `VIBE_CODING_GUIDE` 기준으로 로직 분리(hooks/mockdata) 리팩토링되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 `/settings` 스타일 파일이 feature 전용 CSS에서 전역 스타일 엔트리로 통합되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 `/settings` 일부 패널의 인라인 스타일이 Tailwind 클래스 기반으로 정리되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 `/settings` 2차 정리로 `Agents/Connectors/RBAC` 패널 인라인 스타일이 추가 정비되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.
- 프론트 `/settings` 전체 화면 비율 보정(AppLayout padding 해제 + settings 셸 full-viewport)이 반영되었습니다.
  - 이번 세션에서 AI 서비스 코드/계약 변경은 없습니다.

## 최근 업데이트 (2026-04-22)

- Plan 구현(explainability 병렬 보강 + 기준일시 실사용) 작업은 backend+frontend 범위이며 AI 서비스 코드는 변경하지 않았습니다.

- `POC 010` 기본 점포 및 `기준 일시(기본 2026-03-05 00:00)` UI 추가 작업은 frontend+backend 범위이며 AI 서비스 코드/계약 변경은 없습니다.

- 본사 Settings v3 UI 개편 연계
  - 이번 세션의 코드 변경은 프론트(`/settings`) 화면 개편 중심이며, AI 서비스 코드/계약 변경은 없습니다.

- QA 운영 자산 동기화
  - 기준 QA 마스터 참조 파일을 `../docs/reference/qa-master.csv`로 추가했습니다.
  - QA 실행 이력 기록 도구 `../docs/qa/qa-run-log.py`를 기준 경로로 문서화했습니다.

- QA 안정화 패치 반영
  - `DataExtractionEngine`의 기본 응답 계약을 보강해 `total_sales/peak_hours/top_items/profitability` 질의에서 필수 필드(`total_revenue`, `peak_start`, `peak_revenue_ratio`, `items`, `margin_rate`)를 항상 반환하도록 수정했습니다.

- `POST /analytics/market/insights` no-fallback 정책 반영
  - `MarketInsightService`에서 fallback 응답 생성을 제거했습니다.
  - Gemini/JSON 파싱 실패 시 RuntimeError를 발생시켜 상위 서비스가 오류 계약으로 처리합니다.
  - 계약 스키마(`schemas/contracts.py`)의 `MarketInsightsResponse.source`를 `"ai"` 단일 값으로 고정했습니다.

- `POST /predict` 엔드포인트를 모델 우선 경로로 정비했습니다.
  - `InventoryPredictor`(`inventory_lgbm_model.pkl` + `model_meta.joblib`)를 우선 사용해 `predicted_sales_next_1h`를 산출합니다.
  - 모델 로드/메타 불일치 등 예외 시 기존 DB 휴리스틱 계산으로 폴백합니다.

- 이번 세션의 `/production/status` 주문 마감 시간 표시 보정은 frontend 변경이며 AI 서비스 코드는 변경하지 않았습니다.

- `POST /analytics/ordering/history/insights` 엔드포인트를 추가했습니다.
- `OrderingHistoryInsightService`를 추가해 주문이력 + 운영가이드(RAG) 컨텍스트 기반 Gemini 인사이트를 생성합니다.
- 응답 계약에 `sources`, `retrieved_contexts`, `confidence`를 포함하며, 생성 실패 시 fallback 없이 오류를 반환합니다.

- 이번 세션의 `/sales/metrics` no-fallback 정책은 frontend+backend 오류 처리 정비이며 AI 서비스 코드/계약 변경은 없습니다.

- 이번 세션의 프론트 빌드 오류 복구(타입/차트 formatter 정합)는 frontend 레이어 작업이며 AI 서비스 코드/계약 변경은 없습니다.

- 이번 세션의 `/analytics` fallback 제거는 backend(`metrics`, `sales-trend` 오류 처리 정책) 변경이며 AI 서비스 코드/계약 변경은 없습니다.

- 이번 세션의 `/production/waste-loss`, `/production/inventory-diagnosis` 지연 개선은 백엔드 캐시/타임아웃 정책 조정으로 처리했으며 AI 서비스 코드 변경은 없습니다.
- AI 근거 요약 생성 계약은 유지되며, 백엔드가 시간 제한 내 결과만 선택 반영하도록 호출 정책을 조정했습니다.

- 주문 이력 화면 기본기간/점포검증 개선은 프론트·백엔드 레이어 변경이며 AI 서비스 계약 변경은 없습니다.
- 생산 진단/폐기손실 고도화는 백엔드(`br-korea-poc-backend`)에서 `core_stock_rate`/`core_stockout_time` 기반으로 처리합니다.
- AI 서비스의 생산 API 계약은 기존과 동일하며, 이번 변경으로 인한 AI 라우터 수정은 없습니다.
- `inventory-status` 언패킹 오류(`expected 3, got 2`) 대응은 백엔드 서비스 계층 정규화로 처리했으며 AI 서비스 변경은 없습니다.
- 제품 이미지 미노출 이슈는 프론트 이미지 URL 정규화 로직 보완으로 처리했으며 AI 서비스 변경은 없습니다.
- `inventory-status`의 422 방어(요약 지표 안전 변환)와 `page/page_size` 파라미터 정비는 백엔드 변경이며 AI 서비스 변경은 없습니다.

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
├── run.py                      # FastAPI/uvicorn 서버 실행 엔트리
├── requirements.txt            # 파이썬 의존성 패키지 목록
├── Dockerfile                  # 컨테이너 빌드 설정
├── api/                        # FastAPI 웹 서버 계층
│   ├── main.py                 # 앱 초기화 및 라우터 등록
│   ├── routers/                # 도메인별 API 엔드포인트 (sales, management 등)
│   └── schemas.py              # 요청/응답 Pydantic 모델
├── common/                     # 공통 유틸리티 (Gemini Client, Logger 등)
├── docs/                       # 프로젝트 가이드라인 문서
├── evaluators/                 # AI 응답 품질 평가(LLM-as-a-Judge) 및 환각 감지 모듈
├── models/                     # 학습 완료된 AI 모델 및 스케일러 저장 (.joblib)
├── pipeline/                   # [핵심] AI 모델링 및 데이터 파이프라인 스크립트
│   ├── build_knowledge_base.py # 지식 베이스 임베딩 (pgvector)
│   ├── cluster_stores.py       # 상권 분석 및 매장 클러스터링 (Champion 알고리즘 선정)
│   ├── preprocess.py           # 데이터 마트 생성 및 고급 전처리 (OOS/신제품 보정)
│   ├── train.py                # 실전 배포용 최종 챔피언 모델 학습 및 저장
│   ├── batch_inference_fast.py # 병렬 추론 파이프라인
│   └── generate_insights.py    # 데이터 인사이트 도출 스크립트
├── results/                    # 분석 및 클러스터링 시각화 산출물 보관
├── schemas/                    # 서비스 공통 데이터 계약 모델 (contracts.py 등)
├── services/                   # 핵심 비즈니스 로직 및 AI 에이전트
│   ├── orchestrator.py         # 에이전트 오케스트레이터 (의도 분류 → 도메인 연결)
│   ├── inventory_predictor.py  # LightGBM 기반 시계열 예측 엔진 (Inference)
│   ├── ordering_history_insight_service.py # 주문이력 이상징후 RAG+Gemini 인사이트 생성
│   ├── production_service.py   # 생산 가이드 생성 및 찬스로스 추정 로직
│   └── sales_analyzer.py       # 매출 분석 및 자연어 인사이트 생성 에이전트
└── tests/                      # 단위/통합/검증 테스트 코드
    ├── train_test.py           # 모델별/시나리오별 성능 비교 테스트
    ├── train_val.py            # 6개월 백테스팅 및 ROI(순이익) 시뮬레이션 검증
    ├── evaluate_production.py  # 생산 추정 평가 검증
    ├── mock_payload_generator.py # 통합 테스트 페이로드 생성
    └── verify_orchestrator_apis.py # API 오케스트레이션 검증
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
python pipeline/build_knowledge_base.py
```

## 🚀 AI 모델 학습 및 데이터 파이프라인

본 시스템은 단순한 예측을 넘어 **매장의 수익성을 극대화**하기 위해 상권 분석, 고도화된 전처리, 비즈니스 특화 학습이 결합된 다단계 파이프라인을 운영합니다.

### 1. 학습 프로세스 및 실행 순서

모든 학습 데이터는 정제 후 DB의 `ai_sales_data_mart` 테이블에 캐싱되어 연산 효율을 높입니다.

| 단계 | 실행 스크립트 | 주요 역할 | 실행 주기 |
|:---:|:---|:---|:---:|
| **Step 1** | `pipeline/cluster_stores.py` | 매장별 행동 패턴 분석 및 상권 군집화 (5개 그룹) | 월 1회 또는 필요 시 |
| **Step 2** | `pipeline/preprocess.py` | 마트 생성 (영업시간 필터링, OOS 보정, 신제품 가중치 전환) | 매일 (Daily Batch) |
| **Step 3** | `pipeline/train.py` | 최종 챔피언 모델(LightGBM) 전체 데이터 학습 및 저장 | 주기적 모델 갱신 시 |

---

### 2. 스크립트별 핵심 로직 및 설계 근거

#### **[Step 1] 상권 분석 (`cluster_stores.py`)**
*   **로직**: 매출 규모(Log 스케일링), 시간대별 매출 비중(Morning/Lunch/Afternoon/Evening), 주말 비중, 온라인 매출 비중 등 7개 행동 피처를 기반으로 매장을 그룹핑합니다.
*   **설계 근거**:
    *   **아키텍처 분리**: 매장의 본질적인 성격(상권)은 매일 변하지 않으므로, 무거운 군집화 연산을 일일 배치에서 분리하여 시스템 부하를 최소화했습니다.
    *   **알고리즘 앙상블**: K-Means, DBSCAN, HDBSCAN을 동시 평가하여 **실루엣 계수(Silhouette Score)**가 가장 높은 최적의 알고리즘을 시스템이 스스로 선정합니다.

#### **[Step 2] 데이터 마트 전처리 (`preprocess.py`)**
*   **로직**: 원본 데이터를 로드하여 비영업시간 제거, 행사 제외 4주 순수 평균 산출, 품절(OOS) 데이터 보정, 대형 예약주문(특납) 이상치 제거를 수행합니다.
*   **설계 근거**:
    *   **True Demand 발굴**: 품절로 인해 0으로 기록된 데이터를 과거 평균으로 복원함으로써 AI가 '못 판 수요'까지 학습하도록 유도합니다.
    *   **신제품 Soft Transition**: 데이터가 없는 신제품 출시 초기 14일은 **동일 클러스터 평균**을 100% 참조하고, 이후 28일까지 자기 데이터 비중을 선형적으로 높여가며 예측 안정성을 확보합니다.

#### **[Step 3] 최종 모델 학습 (`train.py`)**
*   **로직**: 백테스팅을 통해 검증된 최적 파라미터로 **가용한 전체 데이터(100%)를 학습**하여 실전용 모델 파일(`.joblib`)을 생성합니다.
*   **설계 근거**: 실전 배포 모델은 가장 최신의 트렌드까지 인지해야 하므로 검증을 위한 데이터 분할 없이 전체 이력을 모두 학습에 투입합니다.

---

### 3. 학습 파라미터 및 전략 선정 기준 (Evaluation)

본 시스템의 학습 파라미터는 단순히 오차(MAE)를 줄이는 것이 아니라, **실질적인 매장 순이익(ROI)**을 기준으로 `tests/train_test.py`와 `pipeline/train_val.py`를 통해 결정되었습니다.

#### **① 알고리즘 선정 기준**
*   **비교군**: LightGBM, XGBoost, RandomForest, CatBoost
*   **평가 결과**: XGBoost가 순수 정확도(MAE)는 소폭 높았으나, **커스텀 손실 함수(Chance Loss) 적용 유연성**과 **학습 속도** 측면에서 비즈니스 최적화 모델로 **LightGBM**을 최종 선정했습니다.

#### **② 패널티 점수(Penalty Weight) 설정 근거**
*   **전략**: "결품으로 손님을 놓치는 비용(65%)이 폐기 비용(35%)보다 크다"는 비즈니스 가정을 수학적으로 모델에 반영했습니다.
*   **검증 지표**: `Net Profit Index` (AI 도입 시 추가 매출 이익 - 추가 폐기 원가)
*   **최종 설정**: 6개월간의 백테스팅을 통해 전체 매장 합계 순이익이 최고점(+60,000 이상)을 기록한 **상권별 가변 패널티(2.0 ~ 2.6)**를 적용했습니다.
    *   *로드샵(Cluster 0)*: 매출 극대화를 위해 패널티 **2.6**
    *   *오피스 상권(Cluster 1)*: 효율적인 재고 관리를 위해 패널티 **2.2** 등

---

## 🛠️ 모델 추론 (Inference) 아키텍처

실시간 API 요청(`POST /api/production/simulation`) 시의 동작 방식입니다.

1.  **Feature Collector**: DB에서 실시간 날씨, 요일, 행사 여부, 4주 평균 등 최신 피처 수집
2.  **Scaling**: 학습 시 사용된 `feature_scaler.joblib`을 적용하여 입력값 정규화
3.  **Predictor**: `advanced_inventory_lgbm.joblib` 모델을 통한 1시간 뒤 예상 수요 도출
4.  **Post-Processor**: 음수 보정 및 예약 주문(특납) 수량 합산 후 최종 추천량 반환


## 현재 상태 메모

- `SalesAnalysisAgent`은 PostgreSQL에 직접 연결해 실제 데이터를 조회합니다. DB 연결 실패 시 기본값 합성 대신 오류 경로를 반환합니다.
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
- `ordering/history` 실데이터 렌더링 보정(프론트 API 파라미터 정합화, 백엔드 `store_id` 필수·에러 우선 정책)은 backend+frontend 범위이며 AI 서비스 코드는 변경하지 않았습니다.

- 상권 화면 에러 배너 조건 조정(메인 market-intelligence 실패 기준)은 frontend 표시 로직 변경이며 AI 서비스 코드는 변경하지 않았습니다.

- 이번 세션의 `/analytics/market` 오류 문구 완화 및 market-intelligence 예외 안전 처리(200 기본 구조 반환)는 backend+frontend 범위이며 AI 서비스 코드는 변경하지 않았습니다.
- 이번 세션의 analytics KPI 0값 보정(`STORE_DEMO`/미존재 점포ID 폴백, 프론트 점포 자동 보정) 역시 backend+frontend 범위 작업이며 AI 서비스 코드는 변경하지 않았습니다.
- 프론트 `.env.example` 기본 점포 ID를 `POC_012`로 조정하고, backend metrics의 빈 기간 자동 폴백을 추가한 작업도 backend+frontend 범위이며 AI 서비스 코드는 변경하지 않았습니다.
- analytics `할인 결제 비중` 소수 정밀도 표시 보정(`0.1%` 미만 2자리 표기)도 backend 표시 포맷 변경이며 AI 서비스 코드는 변경하지 않았습니다.

## Session Update (2026-04-21, Round 2)

- `/generation` 요청 스키마에 `store_id`, `context`를 추가하고 라우터에서 파이프라인 컨텍스트로 전달하도록 확장했습니다.
- `AgentOrchestrator`가 `context.store_id`를 사용해 `ChannelPaymentAnalyzer`/`SalesAnalyzer` 요청의 `store_id`를 `default_store` 고정값 대신 실제 매장 기준으로 전달하도록 수정했습니다.
- RAG 품질평가 입력을 `sources` 문자열 목록이 아니라 실제 검색 컨텍스트(`retrieved_contexts`) 기반으로 평가하도록 정비했습니다.
- `RAGService` 응답에 `retrieved_contexts`를 추가하고 파일 로드/호출 예외를 구체 타입(`OSError`, `JSONDecodeError`, `ValueError`, `TypeError`, `RuntimeError`) 중심으로 정리했습니다.
- `home` 라우터의 동기 함수 `await` 오사용을 `asyncio.to_thread(...)`로 수정해 런타임 `TypeError` 가능성을 제거했습니다.
- `InventoryPredictor` 모델 메타 미로딩 오류 안내 문구를 실제 학습 스크립트 경로(`scripts/train.py`) 기준으로 정정했습니다.
- 오케스트레이터의 생산/주문 분기를 `ProductionService.generate_production_guidance()`/`OrderingService.generate_ordering_guidance()`로 위임하고, 해당 서비스 메서드를 추가했습니다.
- `management` 라우터 연동 안정화를 위해 `normalize_payload_df()`를 `ProductionService` 모듈에 추가했습니다.

## Session Update (2026-04-21, Backend-AI Interface)

- 공통 에러 계약을 추가했습니다. 실패 응답 `detail`은 `error_code/message/retryable/trace_id` 구조를 사용합니다.
- `api/main.py`에 `X-Request-Id` 미들웨어를 추가해 요청 추적 ID를 수신/생성 후 응답 헤더로 반환합니다.
- 계약 버전 확인용 `GET /meta/contract` 엔드포인트를 추가했습니다.
- 주문 마감 알림 batch 조회 `POST /api/ordering/deadline-alerts/batch`를 추가했습니다.

## Session Update (2026-04-21, Role-Based Market Insights)

- `POST /analytics/market/insights` 엔드포인트를 추가했습니다.
- `MarketInsightService`를 도입해 상권 집계 데이터를 기반으로 점주(`store_owner`)와 본사(`hq_admin`) audience별 인사이트를 생성합니다.
- 인사이트 응답은 `executive_summary`, `key_insights`, `risk_warnings`, `action_plan`, `branch_scoreboard`, `report_markdown`, `evidence_refs`, `trace_id`를 포함합니다.
- 프롬프트 가드레일로 입력 데이터 외 외부 사실 생성을 금지하고, 미제공 수치는 `미확인`으로 유도했습니다.
- 주요 경로(`generation/home/router`, `orchestrator`, `rag`, `ordering guide`)에서 광범위 `except Exception`을 축소해 장애 원인 추적성을 높였습니다.

## Session Update (2026-04-21, Round 3)

- 매출 추천 질문 생성(`SalesAnalyzer.suggest_prompts`) 실패 시 `context_prompts` 기반 fallback 주입을 제거했습니다.
- Gemini 호출 실패 시 임시 대체 질문 대신 빈 `prompts`를 반환하도록 변경해, fallback 데이터가 실제 추천처럼 표시되지 않도록 정리했습니다.

## Session Update (2026-04-21, Round 3)

- `OrderingService`의 시뮬레이션 고정 수량 fallback(`150/145/160`)을 제거하고, 과거 실데이터 기반 수량만 사용하도록 정리했습니다.
- 특수 이벤트 옵션 생성 시 과거 1년 데이터가 없으면 임의 증분 수량을 만들지 않고 옵션 추가를 생략하도록 변경했습니다.
- 주문 추천 응답에서 옵션 개수 부족 시 fallback 옵션을 재주입하던 분기를 제거했습니다.

## Session Update (2026-04-22)

- 이번 라운드는 Docker backend 이미지 경로 연결 수정 작업이며 AI 서비스 코드 변경은 없습니다.

## Session Update (2026-04-23, sales metrics incident scope)

- `/sales/metrics` 데이터 미노출 이슈는 backend(`sales/insights` 부분 응답 처리)와 frontend(벤치마킹 에러 완화) 영역에서 수정되었습니다.
- AI 서비스 코드/라우터/스키마 변경은 없으며, 본 세션은 영향도 기록만 수행했습니다.

## Session Update (2026-04-23, signals/sidebar removal scope)

- `/signals` 페이지 제거 및 사이드바 `본사` 메뉴 정리는 프론트엔드 영역 작업입니다.
- AI 서비스 코드/라우터/스키마 변경은 없습니다.

## Session Update (2026-04-23, HQ-as-owner golden queries)

- 본사 담당자 시연용 점주 관점 질의 자산(`br-korea-poc-backend/docs/golden-queries-hq-as-owner.csv`, 200건)이 추가되었습니다.
- AI 서비스 코드 변경은 없으며, 운영 문서/시연 데이터셋 연계 범위만 반영했습니다.

## Session Update (2026-04-23, HQ queries dedup refresh)

- 본사 관점 점주 질의셋이 기존 점주 골든쿼리와 의미 중복 0건 기준으로 재작성되었습니다.
- AI 서비스 코드는 변경하지 않았고, 데이터셋 품질 조건 갱신에 대한 영향도만 문서화했습니다.
## Session Update (2026-04-23, HQ query simplification)

- 본사 관점 점주 질의셋이 초기 시연용으로 단순 문장 중심으로 개편되었습니다.
- AI 서비스 코드는 변경하지 않았고, 데이터셋 품질 개선 영향도만 문서화했습니다.
## Session Update (2026-04-23, HQ query tone simplification)

- 본사 관점 질문셋 문구가 현장 대화형 말투로 단순화되었습니다.
- AI 서비스 코드는 변경하지 않았고 데이터셋 품질 개선 영향도만 문서화했습니다.
## Session Update (2026-04-23, HQ query concrete values)

- HQ 골든쿼리 CSV의 SQL 예시가 실값 치환 형태로 변경되었습니다.
- AI 서비스 코드는 변경하지 않았고 문서 자산 사용성 개선만 반영했습니다.
## Session Update (2026-04-23, HQ query columns split)

- HQ 골든쿼리 CSV의 SQL 컬럼이 일반화/예시 2열로 분리되었습니다.
- AI 서비스 코드는 변경하지 않았고 데이터셋 사용성 개선 영향만 문서화했습니다.

- 백엔드 문서 자산 `../br-korea-poc-backend/docs/golden-queries-new-02.csv`가 추가되었습니다.
  - AI 코드/계약 변경 없이 운영 검증용 질문셋(공통조건+에이전트별 필수/파생 질문) 연동만 반영했습니다.
- `golden-queries-new-02.csv` 운영 참조셋이 112건으로 확장되었습니다.
