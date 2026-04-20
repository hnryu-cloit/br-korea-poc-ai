# AI 서비스 코딩 가이드

## 목적

이 문서는 `br-korea-poc-ai` 레포지토리의 파일 배치, 계층 구조, 네이밍, 코딩 규칙을 일관되게 유지하기 위한 기준이다.

목표는 다음과 같다.

* 라우터, 서비스, 공통 유틸 간 책임을 명확히 분리한다.
* 새 분석 기능을 추가할 때 파일 위치 판단 기준을 통일한다.
* AI를 활용한 코드 생성 및 리팩토링 작업 시에도 동일한 구조 원칙을 유지한다.

---

## 디렉터리 구조

```text
br-korea-poc-ai
├── api
│   ├── main.py
│   ├── config.py
│   ├── dependencies.py
│   └── routers
│       ├── sales.py
│       ├── generation.py
│       ├── home.py
│       └── management.py
├── common
│   ├── logger.py
│   ├── prompt.py
│   ├── gemini.py
│   ├── llm_logger.py
│   ├── query_logger.py
│   ├── rate_limiter.py
│   └── evaluator.py
├── evaluators
│   └── basic.py
├── pipeline
│   └── run.py
├── schemas
│   ├── contracts.py
│   ├── generation.py
│   ├── dashboard.py
│   └── management.py
├── services
│   ├── sales_analyzer.py
│   ├── sales_agent.py
│   ├── channel_payment_analyzer.py
│   ├── inventory_predictor.py
│   ├── orchestrator.py
│   └── ...
├── scripts
│   ├── generate_insights.py
│   └── ...
└── tests
    ├── test_api_integration.py
    └── test_quality_scenarios.py
```

---

## 계층 구조와 책임

### 기본 흐름

```
router → service (또는 pipeline) → common
```

각 계층의 책임은 아래와 같다.

| 계층 | 책임 | 금지 사항 |
|---|---|---|
| `api/routers` | request 파싱, DI, HTTPException 반환 | 분석 로직 직접 구현 |
| `services` | 분석/추론 비즈니스 로직 | LLM 클라이언트 직접 초기화 |
| `pipeline` | 분석 파이프라인 실행 조합 | 외부 요청 직접 처리 |
| `common` | 로거, LLM 클라이언트, 프롬프트, 공통 유틸 | 도메인 분석 로직 |
| `schemas` | 요청/응답 Pydantic 모델 | 로직 포함 금지 |

### router 작성 규칙

* router는 `request` 파싱과 DI 연결만 담당한다.
* 계산/분석 로직은 반드시 service로 위임한다.
* 예외는 router에서 `HTTPException`으로 감싸 반환한다.

```python
@router.post("/query", response_model=SalesQueryResponse)
async def query_sales(
    payload: SalesQueryRequest,
    analyzer: SalesAnalyzer = Depends(get_sales_analyzer),
) -> SalesQueryResponse:
    """자연어 매출 질의를 SalesAnalyzer에 위임해 분석 응답을 반환합니다."""
    try:
        result = await asyncio.to_thread(analyzer.analyze, payload)
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="매출 분석에 실패했습니다.",
        ) from exc
```

### service 작성 규칙

* 분석 로직, LLM 호출 조합, 도메인 판단을 담당한다.
* LLM 클라이언트(`Gemini` 등)는 `__init__`에서 주입받는다.
* 동기 함수는 router에서 `asyncio.to_thread`로 감싸 실행한다.

### pipeline 작성 규칙

* 여러 service를 순서대로 조합하는 실행 흐름을 담당한다.
* 단계별 입출력 타입을 명확히 정의한다.

---

## common 폴더 가이드

`common`은 도메인에 종속되지 않는 인프라성 공통 파일만 둔다.

| 파일 | 역할 |
|---|---|
| `logger.py` | `init_logger()` 및 `timefn` 데코레이터 |
| `gemini.py` | Gemini LLM 클라이언트 래퍼 |
| `prompt.py` | 프롬프트 템플릿 상수 및 생성 함수 |
| `llm_logger.py` | LLM 호출 이력 로깅 |
| `query_logger.py` | SQL 쿼리 로깅 |
| `rate_limiter.py` | API 호출 속도 제한 |
| `evaluator.py` | 공통 평가 유틸 |

도메인 분석 로직, feature 전용 유틸은 `common`에 두지 않는다.

### 넣으면 안 되는 것

* 특정 도메인 분석 함수 (`sales_analyzer`, `inventory_predictor` 등)
* feature 전용 프롬프트 조립 로직
* 특정 API 응답 파싱 함수

---

## 로깅 규칙

* 모듈 최상단에서 `logging.getLogger(__name__)`을 사용한다.
* 앱 전체 초기화가 필요한 경우 `common/logger.py`의 `init_logger()`를 사용한다.

```python
import logging
logger = logging.getLogger(__name__)
```

커스텀 이름이 필요한 경우만 명시한다.

```python
logger = logging.getLogger("sales_analyzer")
```

---

## 네이밍 규칙

### 파일명

* 모든 파일명은 `snake_case.py`를 사용한다.

```text
services/sales_analyzer.py
services/channel_payment_analyzer.py
services/inventory_predictor.py
```

### 스키마(Pydantic 모델) 네이밍

* 응답 모델: `XxxResponse`
* 요청 모델: `XxxRequest`

```python
class SalesQueryRequest(BaseModel): ...
class SalesQueryResponse(BaseModel): ...
```

### 서비스 클래스

* 분석기: `XxxAnalyzer`
* 에이전트: `XxxAgent`
* 예측기: `XxxPredictor`
* 엔진: `XxxEngine`
* 서비스(복합): `XxxService`

---

## 스키마 파일 배치 규칙

* 도메인별로 하나의 파일에 관련 스키마를 모은다.
* 여러 router에서 공유하는 타입은 `schemas/contracts.py`에 둔다.

```text
schemas/contracts.py    # 공통 요청/응답 타입
schemas/generation.py   # 생성 관련 타입
schemas/dashboard.py    # 대시보드 관련 타입
schemas/management.py   # 관리 관련 타입
```

---

## 프롬프트 관리 규칙

* 프롬프트 템플릿 상수는 `common/prompt.py`에서 관리한다.
* 템플릿은 `XXX_PROMPT_TEMPLATE` 형태로 네이밍한다.
* 프롬프트 조립 함수는 `create_xxx_prompt(...)` 형태로 작성한다.

```python
PRODUCTION_ALARM_PROMPT_TEMPLATE = """..."""

def create_production_alarm_prompt(sku: str, current_stock: int, ...) -> str:
    return PRODUCTION_ALARM_PROMPT_TEMPLATE.format(...)
```

도메인에 강하게 결합된 프롬프트는 해당 service 파일 내부에 둘 수 있다.

---

## 의존성 주입(DI)

* DI 팩토리 함수는 `api/dependencies.py`에서 관리한다.

```python
# api/dependencies.py
def get_sales_analyzer() -> SalesAnalyzer:
    return SalesAnalyzer(gemini=Gemini())
```

```python
# router
analyzer: SalesAnalyzer = Depends(get_sales_analyzer)
```

---

## 주석 규칙

* 주석은 한국어로 작성한다.
* WHY가 비자명한 경우에만 작성한다.
* 영어 주석·docstring은 한국어로 전환한다.

### Docstring 스타일 (위치별 구분)

| 위치 | 형식 | 예시 |
|---|---|---|
| `services/` | 명사형 종결, 마침표 없음 | `"""채널 분석 결과 기반 인사이트 생성"""` |
| `api/routers/` | `합니다.` 종결 | `"""분석 결과를 반환합니다."""` |
| `common/prompt.py` | 멀티라인 명사형 | `"""\n프롬프트 생성\n"""` |

단순 getter, 프로퍼티, 1~2줄 함수에는 docstring을 달지 않는다.

### 인라인 주석 스타일

* 한국어, 짧은 명사/동사형으로 작성한다.
* 로직 단계 구분 시 번호를 사용한다: `# 1. ...`, `# 2. ...`
* 비자명한 알고리즘 분기·수치 근거에만 작성한다.
* 섹션 구분이 필요한 경우 아래 패턴을 사용한다.

```python
# ------------------------------------------------------------------
```

---

## 파일 배치 판단 기준

| 질문 | 배치 위치 |
|---|---|
| HTTP 요청/응답 처리 | `api/routers/` |
| 분석/추론 비즈니스 로직 | `services/` |
| 여러 서비스 파이프라인 조합 | `pipeline/` |
| LLM 클라이언트, 로거, 공통 유틸 | `common/` |
| 요청/응답 타입 정의 | `schemas/` |
| 평가 로직 | `evaluators/` |
| 일회성 실행 스크립트 | `scripts/` |

---

## 금지 사항

* router에서 직접 LLM 호출 또는 분석 로직 실행 금지
* `common`에 도메인 분석 로직 포함 금지
* `schemas`에 비즈니스 로직 포함 금지
* 영어 docstring 신규 작성 금지 (기존 코드 한국어로 전환)

---

## 최종 정리

> router는 얇게, 분석 로직은 service로, 공통 인프라는 common으로.

세부 기준:

* 계층 흐름: `router → service → common`
* 파일명: `snake_case.py`
* 스키마: 요청은 `XxxRequest`, 응답은 `XxxResponse`
* 프롬프트 템플릿: `common/prompt.py`에서 `XXX_PROMPT_TEMPLATE` + `create_xxx_prompt()`
* 로깅: `logging.getLogger(__name__)` 또는 `init_logger()`
* 주석: 한국어, WHY 중심, services는 명사형 docstring, routers는 `합니다.` 종결