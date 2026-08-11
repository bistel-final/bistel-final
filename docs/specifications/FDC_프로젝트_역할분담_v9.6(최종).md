# LangGraph 기반 반도체 FDC 이상감지 에이전트 - 역할분담 개정안 (v9.6 최종)

> 총 4명이 팀장·팀원 구분 없이 A·B·C·D 역할을 하나씩 맡는다.
>
> 각 담당자는 자신의 기능에 대해 Backend(FastAPI), AI/Tool, React 화면 연결, 테스트, 평가까지 책임지는 Full-stack 기능 책임 방식으로 개발한다. React 공통 골격은 대혁님이 AI 코딩 도구로 초안만 잡고, 이후 실제 API 연결과 기능 검증은 각 담당자가 직접 수행한다.
>
> PostgreSQL·Neo4j·n8n 기반 인프라와 데이터 적재는 완료되었으므로 기반구축은 역할분담에서 제외한다.
>
> **멘토 원안(`03_시스템_범위_안내.pdf`)에는 React 풀스택이 "범위 밖"으로 명시적으로 적혀 있고, 기본 UI는 Streamlit 7화면이다.** 2026.07.31 멘토링에서 UI를 React+FastAPI로 전환하기로 확정했으므로 본 개정안은 이를 초기 필수 범위로 반영한다. 핵심 도메인 범위(FDC Detection/Classification, HITL, Text2SQL 등)는 멘토 원안을 유지하고 UI·API 구현 방식만 변경한다.

> **문서 적용 우선순위**: 역할·Tool 소유권·Full-stack 책임은 본 역할분담을 따르고, 기능 동작·상태 전이·수용 기준은 배포 데이터 실측과 확정 UI/API 계약을 반영한 `요구사항정의서_v1_9_최종.md`를 우선한다. 구현 구조·DTO·트랜잭션은 `시스템설계서_v1_10_최종.md`를 따른다. v9.6은 8개 업무 화면, 파라미터·추이 중심 알람 대시보드, Trace 검색 계약, 조치·Agent 실행 상세 분리를 역할에 동기화한 버전이다.

## 0. 현재 완료된 기반구축

멘토님이 제공한 `교육생_배포패키지`를 사용해 다음 작업은 완료된 상태다.

- PostgreSQL(pgvector) 컨테이너 실행
- Neo4j 컨테이너 실행
- n8n 컨테이너 실행
- PostgreSQL 스키마 생성
- 기준정보·생산 데이터 적재
- Neo4j 기준정보 관계 적재
- 장비 문서 청킹·임베딩·pgvector 적재(`BAAI/bge-m3`, 1024차원)

따라서 다음 작업은 역할별 일정에 다시 포함하지 않는다.

- Docker Compose 최초 환경구축
- 배포 원본 업무 테이블의 최초 설계·구축
- 기준정보·생산 데이터 생성
- Neo4j 노드·관계 최초 적재
- 장비 문서 최초 임베딩 적재

다만 적재가 완료되었다는 것은 개발이 완료되었다는 뜻은 아니다.

- A는 기존 `fdc_summary`와 `fdc_alarm`을 기준값으로 사용해 요약·판정 로직을 다시 구현해야 한다.
- B는 적재된 Neo4j와 pgvector를 실제 서비스에서 조회하는 로직과 Tool을 구현해야 한다.
- C는 A·B가 제공한 Tool을 LangGraph에 연결하고 HITL·조치 전송 흐름을 구현해야 한다.
- D는 적재된 데이터를 안전하게 활용하는 Text2SQL·통계·차트 계획 기능을 구현해야 한다.

현재 배포된 `04_infra`는 PostgreSQL·Neo4j·n8n까지만 실행한다. FastAPI·React 서비스는 아직 없으며 최종 Compose에 추가한다. LLM은 KOSA 지급 API를 사용하므로 Ollama 컨테이너는 최종 필수 서비스가 아니다(16.2 참고).

## 1. 개발 원칙

### 1.1 Full-stack 기능 책임

각 담당자는 자신의 기능을 다음 순서로 끝까지 개발한다.

```text
DB·AI 로직
→ Service
→ FastAPI Router
→ Pydantic 요청·응답 스키마
→ React 화면
→ API 연결
→ 테스트
→ 평가
```

예를 들어 A는 이상감지 모델 파일만 전달하는 것이 아니라 알람 API와 센서 trace 화면까지 연결한다. B도 문서 검색 함수만 전달하는 것이 아니라 관계·문서 API와 근거 표시 화면까지 완성한다.

### 1.2 하나의 모듈형 애플리케이션

4개의 독립 FastAPI 서버를 만드는 것이 아니라 하나의 FastAPI 애플리케이션에서 모듈을 나눈다. React도 하나의 애플리케이션 안에서 기능 단위로 나눈다.

Backend와 Frontend, 공통 문서와 통합 배포 설정을 **단일 `bistel-final` 모노레포**에서 함께 관리한다. 기능 코드는 폴더로 분리하되 API 계약·문서·Docker Compose는 같은 커밋과 PR에서 갱신할 수 있게 한다.

```text
bistel-final/
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ common/
│  │  ├─ detection/       # A
│  │  ├─ knowledge/       # B
│  │  ├─ agent/           # C
│  │  └─ analytics/       # D
│  ├─ tests/
│  ├─ migrations/
│  ├─ requirements.txt
│  └─ Dockerfile
├─ frontend/
│  ├─ src/
│  │  ├─ app/
│  │  ├─ shared/
│  │  └─ features/
│  │     ├─ detection/    # A
│  │     ├─ knowledge/    # B
│  │     ├─ agent/        # C
│  │     └─ analytics/    # D
│  ├─ package.json
│  ├─ package-lock.json
│  └─ Dockerfile
├─ docs/                  # 요구사항·설계·API·테스트·Trouble Shooting
├─ infra/
│  └─ n8n/
├─ docker-compose.yml
├─ .env.example
└─ README.md
```

각 Backend 기능 폴더에는 최소한 다음 구조를 둔다.

```text
router.py
service.py
schemas.py
repository.py
```

테스트는 기능 폴더 안에 흩어 두지 않고 중앙 `backend/tests/{unit,contract,integration,e2e}` 아래에서 도메인별 파일·하위 폴더로 구분한다.

기능 폴더마다 README를 의무적으로 만들지 않는다. 개발 규칙·API 계약·도메인 설명은 모노레포 루트의 중앙 `docs/`에서 관리하고, 모듈별 문서는 실제로 독립 설명이 필요한 경우에만 추가한다.

### 1.3 React 공통 골격

React 공통 골격은 1주차에 대혁님이 AI 코딩 도구를 사용해 생성한다.

- 공통 레이아웃
- Header·Sidebar
- Router
- API Client
- Loading·Error·Empty State
- Data Table
- Filter
- KPI Card
- Chart Renderer
- Modal·Confirm Dialog

공통 골격을 만든 뒤 각 담당자는 자신의 `features` 폴더에서 화면을 구현하고 실제 API를 연결한다. 공통 컴포넌트를 변경할 경우 다른 기능에 미치는 영향을 확인하고 코드 리뷰를 받는다.

## 2. 최종 역할분담

| 역할 | 담당자 | Backend·AI 담당 | 담당 Tool | React 담당 | 자체 평가 | 목표 채용 직무 | 난이도·강도 |
|---|---|---|---|---|---|---|---|
| A. Detection Full-stack | **신동원** | 요약·규칙·이상감지 모델·알람 API | FDC 요약 조회 | 알람 대시보드·알람·Trace | 규칙 판정·모델 평가(분리) | ML·Data Engineer | 4/5 · 4.5/5 |
| B. Knowledge Full-stack | **강연권** | Neo4j 관계·RAG 검색·근거 API | 관계 조회·문서 검색 | 관계 그래프·문서 근거 | Retrieval 품질 | RAG·Graph Engineer | 4/5 · 4/5 |
| C. Agent Full-stack | **방대혁** | LangGraph·분류·HITL·조치·n8n·배치 트리거 | 조치 전송(멱등) | 조치 목록·Agent 실행 근거·승인 | 분류·HITL·Tool | AI Agent Engineer | 5/5 · 5/5 |
| D. Analytics Full-stack | **천승현** | Text2SQL·통계·차트 계획·감사로그 | Text2SQL 분석 | 자연어 분석·동적 차트·감사로그 | SQL·통계·차트 | AI Backend·Full-stack | 4.5/5 · 4.5/5 |

## 3. A - Detection Full-stack

### 3.1 Backend·AI

- `fdc_trace` 기반 RECIPE STEP별 요약 재계산
- 평균·표준편차·최소·최대 산출
- 기존 `fdc_summary` 1,600건과 재계산 결과 비교
- OOS 판정(R01_OOS): 규격한계(LSL/USL)를 1포인트 이상 이탈
- OOC 판정(R02_OOC): OOS가 아니면서 관리한계(LCL/UCL)를 2포인트 이상 이탈
- 연속 규칙(R03_CONSEC): 판별 키 `(chamber_id, sensor_id, recipe_step_no)`, `chamber_wafer_cum ASC` 기준으로 LOT 경계를 넘어 OOS 연속 횟수를 계산한다. 비OOS에서 재무장하고 최초 3에 도달할 때만 1회 발행한다
- IsolationForest 기반 이상감지 모델. 정상·이상 판정 임계값은 `.env`의 `ANOMALY_SCORE_THRESHOLD`(기본 `0.62`)를 사용한다
- IsolationForest는 anomaly_score와 정상·이상 예측만 제공하고 `fdc_alarm`에 추가 알람을 생성하지 않는다. 알람 51건·incident 10개·조치 10건 기준은 규칙 판정만으로 유지한다
- 모델 저장·불러오기
- 알람 생성·조회 로직
- FDC 요약 조회 Tool
- 규칙 기반 판정 평가와 이상감지 모델(ML) 평가를 분리해서 수행

### 3.2 Tool

```python
get_fdc_summary(lot_hist_id: str) -> dict
```

반환값(멘토 개발 가이드 원안 + `anomaly_*` 3개 필드는 원안 확장):

```python
{
  "ok": bool,
  "wafer": {...},       # WAFER·LOT·장비·챔버 정보
  "sensors": [           # RECIPE STEP별 센서 요약
    {
      "sensor_id": str, "sensor_name": str, "recipe_step_name": str,
      "value_mean": float, "value_std": float, "value_min": float, "value_max": float,
      "spec_lower": float, "ctrl_lower": float, "target": float,
      "ctrl_upper": float, "spec_upper": float, "judgement": str,
    }
  ],
  "anomaly_score": float,      # [원안 확장] 이상감지 모델 점수
  "anomaly_threshold": 0.62,   # [원안 확장] ANOMALY_SCORE_THRESHOLD
  "is_anomaly": bool,          # [원안 확장] score >= threshold
  "reason": str,          # 실패 사유 또는 보충 설명
}
```

**점수 정의(A·C 공통 계약)**: `anomaly_score`는 0~1 범위로 정규화하고, 값이 높을수록 이상도가 높다. A와 C가 동일한 점수 방향과 스케일을 사용한다 — 그래야 A의 `0.62`(이상 여부)와 C의 `0.80`(심각도 보조 신호)을 같은 축에서 비교할 수 있다. `anomaly_*` 필드는 멘토 원안 Tool 반환에는 없지만, C의 `decide_action()`이 점수를 입력으로 쓰기 때문에 의도적으로 확장했다.

`fdc_summary`에는 한 WAFER(`lot_hist_id`)당 센서·RECIPE STEP별 복수 행이 있으므로 Tool은 이 복수 행을 고정된 feature 집계 규칙으로 변환하여 **lot_hist_id당 anomaly_score를 정확히 1개** 반환한다. 시스템설계서 v1.10 5.3의 11개 feature(`target_dev/std_norm/range_norm/ooc_ratio/oos_ratio`의 mean·max + `coverage_ratio`)를 사용하고, 누락 파생값은 학습 그룹 중앙값으로 대체하되 coverage 저하를 별도 feature로 남긴다.

**재현성 규칙**: 정규화 공식(예: min-max의 기준 범위), 모델 버전, 임계값 산정 근거를 코드·문서에 기록한다. 정규화 기준 통계는 학습 데이터에서만 산출하고, 평가 데이터 전체를 이용해 정규화하지 않는다(데이터 누수 방지).

### 3.3 FastAPI

```http
GET /dashboard/summary
GET /summaries/{lot_hist_id}
GET /alarms
GET /alarms/{alarm_id}
GET /traces/catalog
POST /traces/search
```

### 3.4 React

- 알람 대시보드
- 알람 목록
- 알람 상세
- 센서 trace
- 날짜·AREA·장비·챔버·센서·판정 필터
- 알람에서 trace·Agent 분석 화면으로 이동

### 3.5 완료 기준

`알람 51건 재현`은 규칙 기반 판정 결과이고, 이상감지 모델(ML)은 별도 지표로 평가한다. 두 결과를 하나로 섞지 않는다. 규칙 재계산 결과는 51건과 직접 비교하고, ML은 `fdc_alarm` 행을 추가하지 않은 채 anomaly_score·binary 예측의 별도 성능 지표로 평가한다.

- 기존 요약 데이터와 재계산 결과 일치
- **규칙 기반 판정**: R01_OOS·R02_OOC·R03_CONSEC 규칙만으로 제공된 FDC 알람 51건 재현
- **이상감지 모델(ML)**: `anomaly_score`와 정상·이상(binary) 예측만 만든다. 동일 LOT의 WAFER가 학습·평가에 섞이지 않도록 `lot_id` 기준 분리하고 `random_state`를 고정한다. 1차 평가 정답은 `lot_history.fault_code != 'NRM'`이며 Precision·Recall·F1 각 0.80 이상을 비강제 목표로 둔다. 목표 미달만으로 기능 미완료로 판정하지 않고 원인·개선 결과를 기록한다. 2차 검증은 Detection 결과와 `metrology.judgement`(PASS/FAIL)의 연관성을 보조 지표로 확인한다. **Fault Code(FOC/RFM/MFD/TMD) 분류는 A가 아니라 C의 책임이며, `lot_history.fault_code`는 학습·평가 전용 정답 라벨이므로 Agent 판단 입력으로 절대 사용하지 않는다.**
- ML 실행 전후 `fdc_alarm`은 51건으로 유지한다. ML 기반 알람 행 생성은 초기 프로젝트 범위 밖이다
- 잘못된 ID 입력 시 예외 대신 Tool은 `{"ok": false, "reason": "..."}`, FastAPI 엔드포인트는 `404`/`422`로 응답한다(9.2 참고)
- Tool 단독 테스트 통과
- 대시보드·알람·trace 실제 API 연결
- 대시보드 최초 진입 시 `date_from`·`date_to`·계층 필터를 생략해 자동 적용된 전체 데이터 기간 `2026-06-01~2026-06-04`, `reference_date=2026-06-04`, 알람 51건·OOS 37건·OOC 14건을 표시한다. 기간 추이·파라미터 상위 5개·설비별 건수·최근 알람 5건·전체 승인 대기 목록을 Backend 응답 그대로 사용한다
- 이상감지 평가 지표 산출

### 3.6 확보 역량

- 데이터 파이프라인
- 시계열 요약
- 규칙 기반 탐지
- 이상감지 ML 모델
- FastAPI·React 연동
- Function Calling
- 규칙 평가와 ML 평가를 분리 설계하는 능력
- 정답 라벨(`fault_code`)을 Agent 입력과 분리하는 데이터 거버넌스 감각

## 4. B - Knowledge Full-stack

### 4.1 Backend·AI

- 적재된 Neo4j 관계 조회
- AREA·공정·장비·챔버·레시피 관계 조회
- 같은 장비의 다른 챔버 조회
- PHOTO→ETCH 상하류 관계 조회
- 관계 조회 Tool
- pgvector 문서 검색(임베딩 모델은 이미 적재된 `BAAI/bge-m3`, 1024차원을 그대로 사용 — 모델을 바꾸면 재임베딩과 vector 컬럼 차원 변경이 함께 필요하므로 특별한 이유가 없으면 유지한다)
- 장비 모델·문서 유형 필터
- `top_k`·유사도 임계값 조정
- 문서 검색 Tool
- RAG 골드 질문셋 작성
- 관계·검색 품질 평가

### 4.2 Tool 1

```python
get_equipment_context(chamber_id: str) -> dict
```

반환값(멘토 개발 가이드 원안 그대로):

```python
{
  "ok": bool,
  "equipment": {...}, "area": str, "step": str,
  "sibling_chambers": [...], "upstream": [...], "downstream": [...],
  "reason": str,
}
```

### 4.3 Tool 2

```python
search_documents(
    query: str,
    model_code: str | None = None,
    top_k: int = 4,
) -> dict
```

반환값(멘토 개발 가이드 원안 그대로):

```python
{
  "ok": bool,
  "hits": [{"title": str, "section": str, "score": float, "content": str}],
  "reason": str,
}
```

검색 원형 코드는 배포패키지 `05_scripts/load_documents.py`의 `search()` 함수에 이미 있다(`document_chunk` 코사인 유사도 조회 + `model_code` 필터 + `COMMON` 문서 포함). B는 이 함수를 새로 짜지 말고 가져와 Tool 계약에 맞게 감싸는 것부터 시작하면 된다.

### 4.4 FastAPI

```http
GET /relations/chambers/{chamber_id}
GET /relations/equipment/{equipment_id}
POST /documents/search
GET /documents/{document_id}
```

### 4.5 React

- 장비·챔버·공정 관계 그래프
- 관계 노드 선택·필터
- 관련 장비·공정 정보
- 문서 검색
- 검색 결과 점수
- Agent가 사용한 문서 근거 표시

### 4.6 완료 기준

- 챔버 기준 장비·공정·상하류 관계 조회
- 잘못된 ID에서 Tool은 `{"ok": false, "reason": "..."}`, FastAPI 엔드포인트는 `404`/`422`로 응답
- 증상 질문에서 관련 매뉴얼 절 검색
- 검색 결과에 제목·절·점수·내용 포함
- 관계·문서 화면 실제 API 연결
- 문서 검색 골드 10문항 이상에서 Recall@4 ≥ 0.80, MRR ≥ 0.70
- 관계 골드셋 6건(챔버 4건·장비 2건) 정확도 100%. PHO-01-C1의 하류 존재와 ETC-01-C1의 하류 없음·상류 존재 경계를 포함

### 4.7 확보 역량

- Neo4j·Cypher
- Knowledge Graph
- pgvector
- RAG 검색 품질 개선
- FastAPI·React 연동
- Function Calling 2건

## 5. C - Agent Full-stack

### 5.1 Backend·AI

- LangGraph State 정의
- Node·Edge·조건 분기
- A·B가 만든 Tool 연결
- 근거 부족 시 추가 관계·문서 조회
- Tool 호출 횟수·재시도 제한 — `.env`의 `AGENT_MAX_TOOL_CALLS`(기본 `8`), `AGENT_MAX_RETRY`(기본 `3`)를 코드에서 강제한다. 조치 생성 가능 경로는 진단 단계부터 최초 `send_action` 1회분을 예약해 선택 조회가 이를 소비하지 못하게 하고, Tool 호출 수는 HITL 중단·재개와 checkpoint 유실 복구 전후를 합산한다. 단계별 배분·영속 호출 이력 복원·전송 재시도 규칙은 시스템설계서 v1.10 7.4.1을 따른다
- 자율성 레벨 스위치 — `.env`의 `AGENT_AUTONOMY_LEVEL`로 동작을 전환한다. `1`=고정 순서 폴백(합격선), `2`=조건 분기·재시도(기본값·핵심 목표), `3`=완전 자율 ReAct(스트레치, 15.3 참고). 같은 Tool·State로 3단계를 전환할 수 있게 구현한다
- Fault Code 분류(FOC/RFM/MFD/TMD)
- 원인 분석
- 조치 권고
- 규칙 기반 조치 결정(`decide_action()`) — 배포 문서(`TROUBLE_FDC_FaultGuide.md` 5장)의 결정표를 그대로 구현한다:

  | 조건 | 조치 | 승인 |
  |---|---|---|
  | `R03_CONSEC` 발생(연속 3 WAFER) | `EQP_HOLD` | 사람 승인 |
  | 동일 incident 내 DISTINCT `wafer_no` 기준 `OOS` 3 WAFER 이상 | `LOT_HOLD` | 자동 |
  | 동일 incident 내 `OOS` 1~2 WAFER | `NOTIFY` | 자동 |
  | 동일 incident 내 `OOC`만 발생 | `MONITOR` | 자동 |

  **조치 단위와 판정 우선순위**: incident key는 `(lot_id, chamber_id)`이다. 자동 배치는 동일 incident의 알람을 모두 집계하고, OOS 계수는 알람 행 수가 아니라 DISTINCT `wafer_no`로 계산한 뒤 `R03_CONSEC(EQP_HOLD) → OOS 3장 이상(LOT_HOLD) → OOS 1~2장(NOTIFY) → OOC만 발생(MONITOR)` 순으로 조치 1건만 정한다. 순차 처리 중 중간 조치를 전송한 뒤 상향하는 방식은 초기 범위 밖이다.

  **상향 조건**(한 단계 올림): ① 동일 챔버의 동일 `(sensor_id, recipe_step_no, rule_id)` 알람이 직전·현재의 2개 연속 LOT에서 반복, ② 동일 LOT·WAFER에 직접 연결된 `CD_AEI=FAIL`, ③ 동일 LOT·WAFER의 NEXT_STEP 하류 `track_in_at <= alarm.occurred_at`. 세 조건이 겹쳐도 한 단계만 올리고 결과 상한은 `LOT_HOLD`이며, `EQP_HOLD`는 R03_CONSEC 등 챔버 수준 근거로만 결정한다. **하향 조건**: 순수 연쇄 이상은 하류 조치를 생략한다. 그 밖에는 기본 조치가 `NOTIFY`이고 상향 조건이 없으며, 챔버상 바로 다음 실제 처리 WAFER의 동일 판별 키 요약이 IN_CONTROL일 때만 `MONITOR`로 낮춘다. 동일 키의 이후 관측까지 중간 WAFER를 건너뛰지 않는다. 세부 업무 규칙은 요구사항정의서 v1.9 8.2, 판정 DTO·조회 기준과 양성/음성 fixture는 시스템설계서 v1.10 7.7·15.2를 따른다. `SEVERITY_HIGH_THRESHOLD=0.80`은 **보조 위험 신호**일 뿐 단독으로 `EQP_HOLD`를 만들지 않는다

  **"조치하지 않는다"의 DB 처리**: `code_action`에는 `NO_ACTION` 코드가 없으므로 새 코드를 만들지 않는다. 상류 원인으로 설명되어 하류 장비를 조치하지 않는 경우 `agent_run.recommended_action`은 `NULL`로 두고(스키마상 NULL 허용), `action_history` 생성과 `send_action()` 호출을 생략한다. 조치를 생략한 사유는 `agent_run.action_reason`과 감사로그에 기록한다. 단발 정상 복귀 하향은 위 조건을 만족한 기본 `NOTIFY`에만 적용하여 `MONITOR`로 바꾼다
- `EQP_HOLD` HITL Interrupt
- PostgreSQL 체크포인트, `thread_id`를 `agent_run`에 저장
- 승인·반려 후 동일 `thread_id`로 LangGraph 재개
- 조치 전송 Tool(멱등 처리)
- n8n Webhook 연동
- 미처리 `fdc_alarm`을 `(lot_id, chamber_id)` incident로 묶고 **incident당 대표 alarm_id 1건·agent_run 1건**으로 Agent를 실행하는 배치 트리거를 담당한다. **초기 필수 범위의 배치는 스케줄러·주기 폴링이 아니라 관리 명령을 명시적으로 1회 실행하는 방식**으로 확정하고, 자동 주기 실행은 후속 확장으로 둔다. 동일 incident에 포함된 모든 alarm_id를 같은 실행의 처리 대상으로 표시해 후속 배치에서 중복 선택되지 않게 한다. UI의 "분석 실행" 버튼에 alarm_id 1건을 전달해도 소속 incident 전체를 조회하며, 동일 데이터를 즉시 다시 실행하면 신규 Agent 실행·조치·전송이 모두 0건이어야 한다
- `agent_run.alarm_id`에는 incident의 `occurred_at ASC, alarm_id ASC` 첫 알람을 대표로 기록하고, 포함된 전체 alarm_ids는 `agent_run_alarm`과 `agent_run.evidence_json.incident.alarm_ids` 양쪽에 보존한다(시스템설계서 v1.10 4.1)
- 전체 51건 자동 배치는 1단계에서 모든 incident의 기본 조치·상향/하향 조건과 상하류 연결을 `BatchIncidentPlan`으로 먼저 계산하고, 2단계 Agent 실행에서 이 계획을 근거로 결합한다. 따라서 실행 순서와 관계없이 ALM-0031 결과에 같은 LOT의 상류 PHOTO LOT_HOLD 계획 근거가 포함되어야 한다(시스템설계서 v1.10 4.3)
- Agent 실행마다 실제 사용한 모델명 `agent_run.llm_model`과 실제 처리시간 `latency_ms`를 성공·실패 모두 필수 기록한다. `latency_ms`는 LLM·Tool·코드 처리시간 합계이며 HITL 사람 대기시간은 제외한다. `input_tokens`·`output_tokens`는 제공자가 반환할 때 기록한다
- Agent·승인·조치 이벤트 감사로그 기록
- Classification·HITL 평가

### 5.2 Tool

```python
send_action(
    action_id: str,
    agent_run_id: str,
) -> dict
```

멘토 원안의 시그니처는 `send_action(lot_id, equipment_id, chamber_id, action_code, reason)`이지만, 설비·LOT·조치 코드 같은 조치 내용을 LLM이 다시 조합해서 넘기게 하면 값이 틀릴 위험이 있어 `action_id`로 `action_history`를 조회하는 방식으로 의도적으로 바꿨다(안전성 개선). 반환 형식은 원안 그대로 유지한다.

반환값(멘토 개발 가이드 원안 그대로):

```python
{
  "ok": bool,
  "action_id": str,
  "sent": bool,   # 최종 전송 상태: 전송 완료(이번 호출 또는 과거)면 true, 실패·미전송이면 false
  "reason": str,
}
```

`sent`는 "이번 호출이 전송했는가"가 아니라 **최종 전송 상태**다. 새로 전송 성공 → `sent=true`. 이미 `SENT`라서 재전송하지 않음 → `sent=true`(중복 호출임은 `reason`으로 구분: `"이미 전송된 조치이므로 재전송하지 않았습니다."`). 전송 실패·미전송 → `sent=false`. 이렇게 해야 React 화면이 중복 호출 응답을 전송 실패로 오해하지 않는다.

- 조치 내용(설비·LOT·조치 코드·사유)은 `action_history`에서 조회한다.
- 승인 또는 자동조치 결정 시 `action_history` 레코드를 **먼저 생성**한다(11번 참고).
- `action_id`를 멱등성 키로 사용한다.
- `action_id`를 n8n 호출 payload에도 함께 전달해, n8n 쪽에서도 동일 `action_id`로 중복 여부를 판단할 수 있게 한다.
- DB에서 `action_history.send_status`를 `WAITING → SENDING` 또는 `FAILED → SENDING`(제한 횟수 내 재시도)으로 원자적으로 전환하는 데 성공한 호출만 실제 n8n 전송을 수행한다. 이미 `SENDING`·`SENT`·`CANCELED` 상태에서 들어온 호출은 전송하지 않고 기존 상태를 그대로 반환한다(동시 호출·중복 전송 방지, 11.4 상태 전이 참고).
- 전송 성공 시 `SENT`, 실패 시 `FAILED`로 `send_status`를 기록한다.
- Tool 호출의 입력·출력·지연시간·성공 여부(`SUCCESS`/`ERROR`/`TIMEOUT`)는 `agent_tool_call`에 남긴다. `latency_ms` 같은 부가정보는 Tool 반환값이 아니라 이 테이블에 기록한다(9.2 참고).

이렇게 하면 LLM이나 호출 코드가 설비 ID·조치 내용을 다시 조합해서 전송하는 위험이 없어지고, 중복 전송 방지도 `action_id` 하나로 일관되게 처리된다.

### 5.3 중요 규칙

- 알람 발생 여부는 Detection 규칙·모델이 결정한다.
- 심각도와 조치 코드는 규칙 함수(`decide_action()`)가 결정한다. `anomaly_score`(A, 임계값 `0.62`)와 심각도 임계값(C, `SEVERITY_HIGH_THRESHOLD=0.80`)은 서로 다른 값이며 역할도 다르다 — **A는 "이상 여부"를, C는 "심각도·조치·승인 게이트"를 담당한다.**
- 최종 `severity`가 `.env`의 `HITL_REQUIRED_SEVERITY`(기본 `HIGH`)에 해당하면 LangGraph Interrupt를 발생시킨다. 1차 구현에서는 `EQP_HOLD`를 `HIGH`로 매핑해 승인 대상으로 처리한다. `anomaly_score >= 0.80`만으로 `HIGH`나 `EQP_HOLD`를 직접 결정하지 않는다 — 이 환경변수가 `.env` 안전장치와 `decide_action()` 결정표를 잇는 연결 고리다.
- 승인 필요 여부를 LLM에 위임하지 않는다.
- `EQP_HOLD`는 승인 전 전송할 수 없다.
- `MONITOR`·`NOTIFY`·`LOT_HOLD`는 규칙으로 자동 결정된 후 `send_action()`을 호출하고, `EQP_HOLD`는 승인된 경우에만 호출한다. `send_action()`을 호출하지 않는 경우는 두 가지다 — 반려된 `EQP_HOLD`(조치 결정 시 만들어 둔 기존 `action_history`를 `REJECTED/CANCELED`로 갱신)와 상류 원인으로 설명되어 조치를 생략한 경우(`action_history` 행 자체를 만들지 않음).
- `send_action()`은 `action_id` 하나당 실제 전송을 한 번만 수행하며, 나머지 호출은 기존 결과를 그대로 반환한다(멱등성).
- Tool 호출 횟수(`AGENT_MAX_TOOL_CALLS`)와 재시도 횟수(`AGENT_MAX_RETRY`)를 코드로 강제한다. State의 `tool_call_count`는 캐시로 사용하고 `agent_tool_call`을 영속 기준으로 삼아 HITL·checkpoint 유실 복구 시 누적값을 복원한다. 조치 가능 경로의 진단 단계에서는 예약된 최초 `send_action` 1회를 제외한 예산만 사용하며, 전송 재시도는 예약 호출 이후 남은 총예산 안에서만 수행한다.
- 사용한 `llm_model`·Tool 호출·승인·전송 이력을 기록한다.
- `lot_history.fault_code`는 정답 라벨이므로 Agent 판단 입력으로 사용하지 않는다.

### 5.4 FastAPI

```http
POST /agent/runs
GET /agent/runs
GET /agent/runs/{run_id}
GET /approvals
POST /approvals/{approval_id}/decision
GET /actions
GET /actions/{action_id}
```

`POST /agent/runs`는 alarm_id 1건을 받지만 해당 알람만 고립해서 처리하지 않고 소속 `(lot_id, chamber_id)` incident 전체를 조회해 agent_run 1건을 생성한다. FAILED 수동 재실행도 같은 incident 단위로 동작한다.

승인·반려를 별도 엔드포인트로 나누지 않고 `decision`(`APPROVE`|`REJECT`) 하나로 통합한다. 이렇게 해야 11번의 원자적 처리 규칙(같은 승인 건에 대해 한 번만 처리)을 엔드포인트 하나에서 일관되게 검증할 수 있다.

### 5.5 React

- 알람 분석 실행(개별 incident의 수동 실행·FAILED 재실행·시연용). 전체 미처리 incident 배치는 React 화면의 주기 자동 실행이 아니라 관리 명령으로 1회 실행한다
- Agent 실행 상태
- Fault Code·원인·조치 표시
- 사용된 센서·관계·문서 근거
- 조치 목록(승인 대기 기본 필터)
- Agent 실행 근거·승인 상세
- 승인·반려
- 조치 전송 결과

### 5.6 완료 기준

- 알람 1건의 Fault·원인·조치 생성
- Tool 실패 시 그래프 전체가 종료되지 않음
- `EQP_HOLD`에서 실행 중단
- 승인 후 저장된 `thread_id`로 LangGraph 재개, 정상 종료 시 `agent_run=COMPLETED`, 재개·Tool 실패 시 `agent_run=FAILED`
- 승인 전 조치 전송 차단
- 이미 처리된 승인 건 재요청 시 `409 Conflict` 반환
- 같은 `action_id`로 `send_action()`을 두 번 호출해도 실제 전송은 한 번만 발생
- 선택 진단 호출이 발생해도 최초 `send_action` 1회가 보장되고 전체 Tool 시도는 8회 이하. HITL·checkpoint 유실 복구 후 `agent_tool_call`에서 호출 수를 복원해 초기화되지 않으며 전송 재시도도 남은 총예산을 초과하지 않음
- `AGENT_AUTONOMY_LEVEL`을 1→2로 바꿔도 같은 Tool·State로 정상 동작(3은 스트레치)
- 동일한 고정 시나리오 집합에서 Level 1·2의 완료율·평균 Tool 호출 수·지연시간·토큰 사용량을 비교 기록(성능 하한은 두지 않음)
- 성공·실패를 포함한 모든 `agent_run`에 실제 사용한 `llm_model`과 HITL 사람 대기시간을 제외한 `latency_ms`가 기록됨. Main·Dev 모델을 바꿔 실행해도 각 실행에 맞는 값이 남고, 토큰 값은 제공자가 반환할 때 기록
- 승인 화면에서 실제 승인·반려
- n8n 전송 결과와 감사로그 기록
- 미처리 알람의 1회 배치 관리 명령이 중복 실행 없이 동작
- 자동 배치에서 51개 알람이 10개 incident·agent_run 10건으로 처리되고, 대표 alarm_id와 포함 alarm_ids를 추적할 수 있음
- 전체 51건 배치의 incident 순서를 바꾸어도 ALM-0031에 상류 PHOTO LOT_HOLD 근거가 포함됨
- 동일 데이터를 즉시 다시 배치 처리했을 때 신규 Agent 실행·조치·전송이 모두 0건
- `decide_action()`은 OOC, OOS 1~2장, OOS 3장 이상, R03_CONSEC, 상향, 하향, 조치 생략 조건을 테이블 기반 단위 테스트로 검증한다
- Fault 분류는 런타임 incident당 agent_run 1건과 별도로 동일 분류 로직을 fdc_alarm 51개 행(FOC 22 / RFM 15 / MFD 14)의 오프라인 고정 집합으로 전수 평가한다. 각 평가 alarm_id도 런타임과 같은 incident 문맥 생성 로직을 사용하되 agent_run에는 적재하지 않는다. Accuracy·클래스별 Precision·Recall·F1·Macro-F1·혼동행렬을 산출하고 Accuracy·Macro-F1 각 0.80은 비강제 목표로 두며 미달 시 원인·개선 계획을 기록한다. TMD는 배포 표본이 없어 TROUBLE 3.4 기반 합성 fixture로 검증한다

### 5.7 확보 역량

- LangGraph State·Node·Edge
- Tool 오케스트레이션
- Human-in-the-loop, `thread_id` 기반 재개
- 체크포인트
- Function Calling
- n8n·외부 시스템 연동
- 환경변수 기반 안전장치 설계(자율성 레벨, 호출 상한, 승인 게이트 임계값 분리)
- 멱등성·트랜잭션 설계
- 배치 트리거·중복 실행 방지 설계
- 모델 사용 이력(llm_model·토큰·지연시간) 기록을 통한 재현성 확보

## 6. D - Analytics Full-stack

### 6.1 Backend·AI

- 자연어 질문 의도 분석
- 자연어→SQL 변환
- LLM이 생성한 SQL은 반드시 `.env`의 `READONLY_USER`(`kosa_readonly`) 계정으로 실행한다. 일반 `/analytics/query`는 승인·조치 화면과 같은 최신 상태를 보도록 운영 `kosa_agent` DB의 허용 테이블을 조회하고, 골드·방어 평가는 배포 초기 상태를 보존한 별도 `kosa_text2sql` 평가 DB에서만 실행한다
- 운영·평가 `nl_query_log`는 각각 대상 DB의 고정 QueryLogRepository와 별도 최소권한 writer 계정으로 기록한다. 일반 API가 평가 DSN을 사용하거나 평가 실행이 운영 DSN을 사용하는 경로는 금지한다
- 생성 SQL은 `sqlglot`으로 파싱해 단일 `SELECT`만 허용하고, 쓰기 구문·다중 문장·허용되지 않은 테이블·컬럼·위험 함수·시스템 카탈로그 접근을 차단한다. CTE·서브쿼리를 포함한 AST 전체를 재귀 검사한다
- 읽기 의도의 구문·스키마·컬럼 오류만 교정 재생성을 1회 허용한다(최초 포함 총 2회). 쓰기·다중 문장·비허용 테이블·위험 함수·시스템 카탈로그 등 정책 위반은 재생성 없이 즉시 거부하며, D의 재생성 제한은 Agent의 `AGENT_MAX_RETRY`와 분리한다
- 조회 행 수·실행 시간 제한
- 통계 유형 구조화 출력
- 차트 유형 구조화 출력
- 통계·차트 호환성 검증
- Text2SQL 분석 Tool
- 감사로그 조회·검색·통계
- Text2SQL 골드 질문셋
- SQL·통계·차트 평가
- 독립 Analytics 호출 이력은 `nl_query_log`에 기록한다. D Tool을 추후 Agent에 연결한 경우에만 해당 호출을 `agent_tool_call`에도 기록한다

### 6.2 Tool

```python
generate_analysis_plan(question: str) -> dict
```

D의 분석 필드(`sql`, `metric`, `group_by`, `visualization`)는 자체 확장이지만, 성공·오류 계약은 다른 Tool과 동일하게 최상위 `ok`와 `reason`을 사용한다(9.2 공통 계약과 통일).

출력 예시:

```json
{
  "ok": true,
  "sql": "SELECT lh.chamber_id, AVG(fs.value_mean) AS avg_value FROM fdc_summary AS fs JOIN lot_history AS lh ON lh.lot_hist_id = fs.lot_hist_id GROUP BY lh.chamber_id",
  "metric": {
    "type": "mean",
    "column": "avg_value"
  },
  "group_by": [
    "chamber_id"
  ],
  "visualization": {
    "chart_type": "bar",
    "x": "chamber_id",
    "y": "avg_value"
  },
  "reason": ""
}
```

실패 시:

```json
{
  "ok": false,
  "reason": "생성된 SQL에 허용되지 않은 테이블이 포함되어 있습니다."
}
```

LLM은 SQL·통계 방식·그룹 기준·차트 유형을 구조화해서 제안한다. 실제 평균·중앙값·표준편차·백분위 계산은 SQL 또는 Python 코드가 수행한다.

`fdc_summary`에는 `chamber_id` 컬럼이 없으므로 챔버 기준 분석은 위 예시처럼 `lot_history`를 조인한다. 문서의 예제 SQL도 실제 화이트리스트·스키마 검증을 통과하는 쿼리만 사용한다.

멘토 원안의 Agent 핵심 Tool은 A·B·C가 소유한 `get_fdc_summary`, `get_equipment_context`, `search_documents`, `send_action` **4종**이다. `generate_analysis_plan`은 D의 자연어 분석 화면을 Function Calling·구조화 출력으로 구현하기 위해 추가한 **독립 Analytics Tool 1종**이므로 공통 Tool 계약에서는 총 5종으로 관리한다. 초기 범위에서 LangGraph가 호출하지 않으며 `AGENT_MAX_TOOL_CALLS=8`과 `agent_tool_call` 집계에는 포함하지 않는다.

Backend에서는 다음을 검증한다.

- `sqlglot`으로 파싱해 SELECT만 허용
- 다중 SQL 차단
- 허용된 테이블·컬럼만 사용
- 위험 함수와 시스템 카탈로그 접근 차단
- CTE·서브쿼리 안의 쓰기·DDL·SELECT INTO·잠금 구문까지 재귀 차단
- 조회 행 수 제한
- 실행 시간 제한
- 통계 유형 enum 검증
- 차트 유형 enum 검증
- 숫자 컬럼이 없는 히스토그램 차단
- 시간축이 없는 선 그래프 보정

### 6.3 FastAPI

```http
POST /analytics/query
POST /analytics/validate
GET /analytics/evaluations
GET /audit-logs
```

### 6.4 React

- 자연어 질문 입력
- 생성 SQL 확인
- 조회 결과 표
- 평균·중앙값·표준편차·백분위 표시
- 막대·선·히스토그램 차트
- 감사로그 검색·필터
- 이벤트 유형별 통계

### 6.5 완료 기준

- 골드 질문 12건 중 10건 이상(≥83%) 정답. 실수 절대오차 0.001, 정렬 요구 문항만 순서 비교, 시각화 문항은 결과와 chart_type·x·y까지 비교
- 운영 LLM 생성 SQL은 `kosa_readonly`로 운영 `kosa_agent`의 현재 허용 테이블만 조회하며, `action_history` 결과가 전용 조치 화면의 현재 값과 일치한다. 골드 평가는 운영 상태를 참조하지 않고 `kosa_text2sql` 기준 DB에서만 실행한다
- 두 실행 경로 모두 INSERT·UPDATE·DELETE·DROP을 차단하고, 실행 결과·거부 이력은 같은 목적 DB의 별도 최소권한 writer가 고정 INSERT로 기록한다
- 읽기 의도의 교정 가능한 SQL 오류는 최대 1회만 재생성하고 두 번째 검증도 실패하면 `{ok:false, reason}`으로 종료
- 정책 위반 SQL은 재생성·실행 없이 즉시 거부
- 쓰기·다중 문장·비허용 테이블·존재하지 않는 컬럼·위험 함수·시스템 카탈로그 방어 6종을 모두 안전하게 처리
- 통계 방식과 결과 컬럼 일치
- 차트 유형과 데이터 구조 호환
- 자연어 분석 화면 실제 API 연결
- 독립 Analytics Tool의 성공·실패·거부 이력이 `nl_query_log`에 기록됨
- 감사로그 조회·필터 동작

### 6.6 확보 역량

- Text2SQL
- SQL 안전장치(`sqlglot`, 읽기 전용 계정)
- LLM Structured Output
- Function Calling
- 통계 분석
- 동적 시각화
- FastAPI·React 연동
- 정확도 평가

## 7. 대시보드 개발 전략

대시보드는 프로젝트 범위에 포함한다. 다만 운영 대시보드와 자연어 동적 분석을 구분한다.

### 7.1 A 담당 - 알람 대시보드

알람 대시보드는 항상 같은 기준의 고정 지표와 안정 정렬된 목록을 SQL과 규칙으로 계산한다.

- 조회 기준일 알람 수
- OOS·OOC 건수
- 일자별 OOS·OOC 추이와 R03 발생 여부
- 알람이 많은 파라미터 상위 5개
- 설비·챔버별 알람 건수
- **승인 대기 목록** — 이 데이터는 C가 소유한 `approval_request`의 결과다. A는 승인 테이블을 재구현하지 않고 C의 `ApprovalService.list_pending()`을 사용한다
- 최근 알람 5건

권장 API:

```http
GET /dashboard/summary?date_from=2026-06-01&date_to=2026-06-04&area=ETCH
```

`/dashboard/summary`의 승인 대기 목록은 날짜·AREA·설비·챔버 필터와 무관한 전체 PENDING이며 `requested_at DESC, approval_id DESC`로 정렬한다. A의 서비스 코드 내부에서 C의 공유 서비스 함수를 호출해 채우고, 승인 조회 로직을 중복 구현하지 않는다.

`date_from`·`date_to`를 모두 생략하면 선택한 AREA·설비·챔버 범위의 알람 최소·최대 일자를 자동 적용하고, 한쪽만 지정하면 해당 계층의 데이터 경계로 보완한다. API는 실제 적용 기간을 `date_range`, 기간 안의 최신 데이터 일자를 `reference_date`로 반환한다. React 최초 진입은 전체 계층·기간 경계를 생략하여 `2026-06-01~2026-06-04`와 51건을 표시하고, 사용자가 기간을 선택한 뒤에는 계층 필터를 바꿔도 선택 기간을 유지한다. 해당 기간에 데이터가 없으면 Empty 상태를 표시한다.

대시보드 집계·정렬은 다음으로 고정한다.

- 알람·OOS·OOC는 Asia/Seoul 기준 조회일의 `fdc_alarm` 행을 계수하고 AREA는 `dim_chamber.area_id`로 판별한다. 한 WAFER의 복수 규칙 알람도 각각 계수한다
- 일자별 추이는 날짜 오름차순이며 OOS·OOC 건수와 R03_CONSEC 존재 여부를 포함한다
- 파라미터 상위 목록은 `alarm_count DESC, sensor_id ASC` 상위 5건이다
- 설비·챔버별 건수는 ID 오름차순으로 안정 정렬한다
- 최근 알람은 조회일·AREA 범위의 `occurred_at DESC, alarm_id DESC` 상위 5건이다
- 승인 대기 목록은 날짜·AREA와 무관한 전체 `approval_request.status='PENDING'`이며 C의 공유 ApprovalService를 사용한다

알람 대시보드는 LLM을 사용하지 않는다. 계측 PASS율과 챔버 상태 카드는 이 화면의 초기 범위에서 제외하고, 필요한 추가 통계는 D의 자연어 분석 화면에서 조회한다.

### 7.2 D 담당 - 자연어 동적 분석

자연어 동적 분석은 사용자의 질문에 따라 SQL·통계·차트 구성이 달라진다.

예시:
- "ETCH 챔버별 최근 OOS 비율을 보여줘."
- "PHOTO 포커스 센서의 95백분위를 비교해줘."
- "최근 10개 WAFER의 평균 압력을 선 그래프로 보여줘."

React에서는 별도 `/analytics` 화면으로 제공하고 알람 대시보드에서 이동 링크만 제공할 수 있다.

```text
알람 대시보드(/dashboard) - A
자연어 분석(/analytics)    - D
```

두 기능은 계약·책임을 분리하되 공통 내비게이션으로 연결한다.

### 7.3 대시보드 우선순위

대시보드는 기획 범위에 처음부터 포함하되 실제 구현은 핵심 Agent·HITL 흐름보다 우선하지 않는다. A의 알람 대시보드(고정 집계)와 D의 자연어 분석(가변 질의)은 8개 화면 계약 안에서 독립적으로 개발한다.

- 1주차: KPI·필터·API 응답 스키마 확정, Mock 화면 생성
- 2~3주차: A가 조회 API와 고정 지표 구현 / D는 자기 일정대로 자연어 분석·동적 차트 기능을 독립적으로 완성
- 4주차: 알람 대시보드 실제 API 연결(승인 대기 목록은 C의 공유 서비스를 호출)
- 5주차: `/dashboard`와 `/analytics`의 공통 필터·이동 흐름·디자인 통합
- 6주차: 디자인·통합 테스트

## 8. n8n을 A에서 C로 이동한 이유

초기 자료의 업무분담 표에는 A 영역에 `n8n 자동화`가 포함되어 있었지만, 실제로는 A가 아니라 C가 담당하는 것이 맞다.

전체 흐름은 다음과 같다.

```text
A: 센서 이상 감지·알람 생성
→ C: 관계·문서 근거를 사용한 Fault 분류
→ C: 조치 코드 결정(MONITOR / NOTIFY / LOT_HOLD / EQP_HOLD)
→ C: MONITOR·NOTIFY·LOT_HOLD면 자동으로 send_action(action_id, agent_run_id) 호출
→ C: EQP_HOLD면 승인 대기 → 승인된 경우에만 send_action() 호출
→ C: n8n Webhook 호출
→ MES·메일 모의 전송
→ 감사로그 기록
```

**n8n은 Agent가 확정한 조치를 외부 시스템에 실행하는 경로다. `MONITOR`·`NOTIFY`·`LOT_HOLD`는 자동으로 전송하고, `EQP_HOLD`는 HITL 승인 후 전송한다. 따라서 이상을 감지하는 A보다 조치 분기·승인·실행을 담당하는 C가 맡는 것이 적절하다.**

배포된 `docker-compose.yml`의 n8n 서비스 주석에는 "알림 발송 자동화 (승인된 건만 발송)"이라고 적혀 있는데, 이는 자동조치(MONITOR·NOTIFY·LOT_HOLD)를 다루지 않아 실제 동작과 다르다. React+FastAPI 통합 시 이 주석을 다음처럼 고친다.

> 확정된 자동조치(`MONITOR`·`NOTIFY`·`LOT_HOLD`)와 승인된 `EQP_HOLD`만 발송

변경 효과:
- 승인되지 않은 `EQP_HOLD` 전송 위험 감소
- Detection과 Action 사이의 불필요한 책임 중복 제거
- HITL→전송→감사로그를 한 담당자가 끝까지 테스트 가능
- 장애 발생 시 담당 범위 명확화
- `send_action()`과 n8n Webhook의 변경을 같은 담당자가 관리

## 9. Tool 소유권과 1주차 계약 동결

### 9.1 Tool 소유권

| Tool | 구현 담당 | 주요 사용자 |
|---|---|---|
| FDC 요약 조회 | A | C의 LangGraph |
| 관계 조회 | B | C의 LangGraph |
| 문서 검색 | B | C의 LangGraph |
| 조치 전송 | C | C의 LangGraph |
| Text2SQL 분석 | D | 독립 분석 기능, 추후 Agent 연결 가능 |

C의 핵심 LangGraph는 A가 제공하는 FDC 요약 Tool과 B가 제공하는 관계·문서 Tool을 사용하고, C가 직접 구현한 조치 전송 Tool(`send_action(action_id, agent_run_id)`)을 호출한다. 조치 전송 Tool은 입력에 설비·조치 내용을 받지 않고 `action_id`로 `action_history`를 조회하는 방식이므로, LLM이 조치 내용을 다시 만들어낼 여지가 없다.

D의 Text2SQL 분석 Tool은 기본적으로 독립 데이터 활용 기능이다. 핵심 Agent 흐름이 완성된 후 필요할 경우 LangGraph에 연결한다.

따라서 문서의 Tool 수가 원안 4종에서 5종으로 보이는 것은 Agent 범위를 확대한 것이 아니라 **원안 Agent Tool 4종 + 독립 Analytics Tool 1종을 하나의 계약 표에 함께 표시한 것**이다. D Tool은 초기 범위에서 C의 호출 예산·Agent 로그와 분리한다.

### 9.2 1주차 Tool 계약 체크포인트

1주차 말까지 다음 항목을 확정한다.

- 함수명
- 입력 Pydantic 모델
- 출력 Pydantic 모델
- 필수·선택 필드
- ID 형식
- 정상 응답
- 오류 응답
- 타임아웃 처리 — LangGraph가 호출하는 A·B·C Tool은 타임아웃 시 예외로 그래프를 종료하지 않고 `{"ok": false, "reason": "TIMEOUT: ..."}`를 반환하며, `agent_tool_call.status='TIMEOUT'`으로 기록한다. 재시도는 `AGENT_MAX_RETRY`(기본 3)와 총 8회 잔여 예산 안에서만 수행한다. 독립 D Tool은 Agent 재시도 변수를 사용하지 않고 Analytics 파이프라인의 LLM timeout·교정 재생성 제한을 따른다
- 예제 JSON

**Tool 반환 형식은 멘토 개발 가이드 원안을 그대로 따른다.** 공통 래퍼(`data`/`error`/`meta` 같은 중첩 객체)를 새로 만들지 않는다.

```python
# 성공
{"ok": True, ...도구별 결과 필드..., "reason": "..."}

# 실패 — 예외를 던지지 않고 정상 반환한다
{"ok": False, "reason": "lot_hist_id를 찾을 수 없습니다."}
```

`latency_ms`·호출 성공 여부(`SUCCESS`/`ERROR`/`TIMEOUT`) 같은 부가정보는 Tool 반환값에 넣지 않는다. Agent 흐름에서 호출되는 A·B·C Tool은 `agent_tool_call`에 기록하고, 독립 Analytics인 D Tool은 `agent_run_id`가 없으므로 `nl_query_log`에 기록한다. D Tool을 추후 Agent에 연결한 경우에만 `agent_tool_call`에도 기록한다(10.3 참고).

이 `ok=false` 규약은 **Tool 함수 반환값** 기준이다. 같은 상황을 FastAPI 엔드포인트로 직접 호출하면 HTTP 상태코드로 구분한다 — 존재하지 않는 ID는 `404`, 요청 형식 자체가 잘못된 경우는 `422`를 반환한다. Tool과 REST API는 오류 표현 방식이 다르다는 점을 헷갈리지 않는다.

계약 동결 후 스키마를 변경할 경우 PR과 테스트 변경을 통해 공유한다. 구두 전달만으로 Tool 계약을 변경하지 않는다.

### 9.3 AI 모델·임베딩 확정사항

배포 `.env.example`의 Ollama 값은 사용하지 않는다. 2026.07 팀 합의에 따라 **최종 프로젝트 LLM은 KOSA 지급 API 키를 사용한다.** 복사 실수를 막기 위해 프로젝트 `.env.example`에는 실제 비밀값 대신 아래 placeholder만 둔다. 제공자와 모델은 `.env`로 추상화하고 실제 실행 모델명은 `agent_run.llm_model`에 기록한다.

```text
LLM_PROVIDER=<KOSA 제공 provider>
LLM_MODEL_MAIN=<KOSA 제공 모델명>   # 최종 판단
LLM_MODEL_DEV=<KOSA 제공 모델명>    # 개발·디버깅 루프(동일 모델 사용 가능)
LLM_API_KEY=
LLM_BASE_URL=<KOSA 제공 base URL>
LLM_TEMPERATURE=0.1
EMBEDDING_MODEL=BAAI/bge-m3          # 이미 이 모델로 문서가 적재되어 있음(1024차원)
EMBEDDING_DIM=1024
```

주의할 점:

- **임베딩 모델은 사실상 확정이다.** 이미 `bge-m3`/1024차원으로 문서가 pgvector에 적재되어 있으므로, 특별한 이유 없이 바꾸면 재임베딩과 vector 컬럼 차원 변경이 함께 필요하다.
- **Ollama 기본값은 사용하지 않는다.** KOSA 지급 API의 실제 provider·model·base URL을 각 팀원의 `.env`에 설정하고 키는 커밋하지 않는다. 최종 실행 모델명은 평가 결과와 agent_run에 함께 남긴다.
- 구조화 출력 방식(Function Calling vs JSON mode 등)은 C·D가 같은 방식을 쓸지 1주차에 정한다.
- API 키·시크릿 관리 방식(환경변수, `.env`, 커밋 금지 규칙)도 1주차에 재확인한다.

## 10. 감사로그 책임과 이벤트 규칙

`audit_log` 테이블은 멘토님 스키마에 이미 정의되어 있으므로 새로 설계하지 않는다. 멘토 확인 기준(`04_개발_가이드.pdf`)은 "감지·판단·승인요청·승인·전송" 5개 이벤트가 남는 것이다. 아래 9개 이벤트는 이 5개를 포함하는 더 세분화된 버전이다. 1주차에 A·C·D가 이벤트 작성 규칙을 확정한다.

### 10.1 역할별 책임

- A: Detection 완료 이벤트 기록
- C: Agent·승인·조치 이벤트 기록
- D: 감사로그 조회·검색·통계 API와 화면
- `agent_tool_call`: Tool 호출 상세 기록(입력·출력·지연시간·`SUCCESS`/`ERROR`/`TIMEOUT`)
- `audit_log`: 주요 상태 변경 이벤트 기록

### 10.2 권장 이벤트

| 이벤트 | 작성 담당 | 주요 Entity | 멘토 원안 5종 매핑 |
|---|---|---|---|
| `DETECTION_COMPLETED` | A | `LOT_HIST`(`lot_hist_id`, 해당 WAFER의 FDC 요약·규칙 재계산 완료) | 감지 |
| `AGENT_RUN_STARTED` | C | `agent_run` | - |
| `CLASSIFICATION_COMPLETED` | C | `agent_run` | 판단 |
| `APPROVAL_REQUESTED` | C | `approval_request` | 승인요청 |
| `APPROVAL_DECIDED` | C | `approval_request` | 승인 |
| `ACTION_SENT` | C | `action_history` | 전송 |
| `ACTION_SEND_FAILED` | C | `action_history` | - |
| `AGENT_RUN_COMPLETED` | C | `agent_run` | - |
| `AGENT_RUN_FAILED` | C | `agent_run` | - |

순수 연쇄 이상으로 하류 조치를 생략할 때는 고정 9종 외 `ACTION_SKIPPED`를 추가하지 않는다. `CLASSIFICATION_COMPLETED.after_json`과 `AGENT_RUN_COMPLETED.detail`에 `recommended_action=NULL`과 생략 사유를 기록한다.

### 10.3 기록 원칙

- `audit_log`는 append-only로 사용한다.
- 감사로그를 수정·삭제하지 않는다.
- `detail`에는 사람이 읽을 설명을 저장한다.
- 변경 전·후 구조화 데이터는 `before_json`·`after_json`에 저장한다.
- Agent 흐름의 A·B·C Tool 입력·출력·지연시간·오류는 `agent_tool_call`에 저장한다. 독립 D Analytics 호출은 `nl_query_log`에 저장한다.
- 같은 정보를 `audit_log`와 `agent_tool_call`에 중복 저장하지 않는다.

## 11. 승인 처리 — 원자적·멱등적 정의

승인 관련 테이블은 역할을 명확히 구분한다.

- `approval_request`: HITL 승인 요청과 결정의 기준 테이블. `status`는 `PENDING`/`APPROVED`/`REJECTED`/`EXPIRED`를 그대로 사용한다(변경 없음)
- `action_history`: 최종 조치·반려·전송 결과 이력. `action_id`가 멱등성 키. `send_status`는 실제 데이터 기준으로 `WAITING`을 사용한다(11.4 참고)

`approval_request.status`의 `PENDING`과 `action_history.send_status`는 서로 다른 필드다. 이번 개정에서 바뀐 것은 `send_status` 쪽 값(`PENDING`→`WAITING`)뿐이며, `approval_request.status`의 `PENDING`은 그대로 유지한다.

### 11.1 승인 처리 API

```http
POST /approvals/{approval_id}/decision
```

요청 본문:

```json
{
  "decision": "APPROVE",
  "decided_by": "demo.user",
  "decision_comment": "설비 정지 승인"
}
```

- `decision`은 `APPROVE` | `REJECT`. `decided_by`·`decision_comment`는 `approval_request`의 동명 컬럼에 그대로 저장한다(스키마에 이미 존재). 인증·로그인은 원안 범위 밖이므로 만들지 않지만, 감사로그의 "누가 승인했는가"는 이 필드로 남긴다.
- `approval_request.status`가 `PENDING`일 때만 처리한다.
- 이미 처리되었거나 만료된 요청(`APPROVED`/`REJECTED`/`EXPIRED`)에 다시 호출하면 재처리하지 않고 `409 Conflict`를 반환한다.
- 승인 상태 변경은 `UPDATE ... WHERE approval_id=%s AND status='PENDING' RETURNING ...` 형태의 **조건부 갱신**으로 수행한다. 갱신된 행이 0건이면 이미 처리된 요청이므로 `409 Conflict`를 반환한다. `SELECT` 후 별도 `UPDATE`하는 방식은 동시 승인 요청 2개가 모두 통과할 수 있으므로 사용하지 않는다.

승인 요청을 시간 경과로 `EXPIRED`로 전환하는 자동 만료 스케줄러는 초기 범위 밖이다. `EXPIRED`는 기존 데이터 또는 격리 테스트 fixture의 터미널 상태로만 처리하며, 승인 API는 approval_request·agent_run·action_history를 변경하지 않고 409를 반환한다.

### 11.2 트랜잭션 경계

`action_history`는 승인 결정 시 새로 만들지 않고 **조치 결정 시 먼저 만든다.** 배포 데이터의 ACT-0005·ACT-0010이 승인 전 `PENDING/WAITING` 상태로 존재하고, 51알람→10incident→10조치를 승인 결정 전에 재현해야 하므로 v9.3의 “승인·반려 결정 시 생성” 규칙을 다음과 같이 교체한다.

**PostgreSQL Checkpoint 초기화**: 배포 `01_schema.sql`에는 LangGraph 체크포인트 테이블이 없다. `langgraph-checkpoint-postgres`의 `PostgresSaver.setup()`이 수행하는 테이블 생성·내부 마이그레이션은 원본 스키마 수정이 아니라 별도 1회 초기화 작업으로 관리한다. 공용 DB에서 FastAPI 시작 때마다 자동 실행하지 않으며, 시스템 설계서에 대상 테이블·실행 계정·팀 공유와 멘토 확인·재실행 안전성·검증·복구 절차를 정한 뒤 쓰기 계정으로 실행한다.

**EQP_HOLD 조치 결정 트랜잭션**

- `action_history` 1건 생성: `approval_required='Y'`, `approval_status='PENDING'`, `send_status='WAITING'`, `approved_by=NULL`, `approved_at=NULL`, `send_channel='MES'`
- `approval_request` 1건 생성: `status='PENDING'`
- `agent_run.status='WAITING_APPROVAL'`로 갱신
- `audit_log`에 `APPROVAL_REQUESTED` 기록
- 커밋 후 LangGraph를 interrupt한다

**자동조치 결정 트랜잭션**

- MONITOR·NOTIFY·LOT_HOLD의 `action_history`를 `approval_status='AUTO'`, `send_status='WAITING'`, `approved_by='system'`, `approved_at=created_at`으로 생성한다
- 전송 채널은 MONITOR·NOTIFY → `EMAIL`, LOT_HOLD → `MES`로 확정하며 커밋 후 `send_action()`을 호출한다

**승인·반려 결정 트랜잭션**

- `approval_request` 상태 변경(`PENDING` → `APPROVED`/`REJECTED`) + `decided_by`·`decided_at=now()`·`decision_comment` 기록
- `agent_run` 상태 변경(`WAITING_APPROVAL` → `RUNNING`)
- 새 action_history를 만들지 않고 기존 행을 갱신한다. 승인 시 `approval_status='APPROVED'`, `send_status='WAITING'`, `approved_by=approval_request.decided_by`, `approved_at=approval_request.decided_at`; 반려 시 `approval_status='REJECTED'`, `send_status='CANCELED'`, `approved_by/approved_at=NULL`
- `audit_log`에 `APPROVAL_DECIDED` 기록

승인·반려 행위자는 approval_request와 audit_log에 항상 남기며, `action_history.approved_by/approved_at`은 실제 승인된 경우에만 채운다. 이 트랜잭션에는 LangGraph 재개나 n8n 호출을 포함하지 않는다. 둘 다 승인 커밋 **이후**에 실행한다(11.3 참고). n8n 호출이 실패해도 이미 커밋된 승인 결정 자체는 되돌리지 않고, 기존 `action_history.send_status='FAILED'`로만 기록한다. 재시도는 동일 `action_id`를 사용한다(11.4 참고).

### 11.3 전체 흐름

재개된 그래프가 "승인인지 반려인지, 어떤 `action_id`를 전송해야 하는지"를 알 수 있도록 LangGraph State에 최소 다음 필드를 둔다. `action_history`에는 `agent_run_id` 컬럼이 없으므로, `action_id`를 State에 주입하지 않으면 재개된 그래프가 자신의 조치 레코드를 안전하게 찾을 수 없다.

```python
class AgentState(TypedDict):
    ...
    approval_id: str | None
    approval_decision: str | None   # "APPROVE" | "REJECT"
    action_id: str | None
```

```text
1. C가 agent_run 생성, thread_id를 agent_run에 저장
2. EQP_HOLD이면 같은 트랜잭션에서 action_history(PENDING/WAITING)와 approval_request(PENDING)를 생성하고 agent_run을 WAITING_APPROVAL로 변경
3. action_id를 LangGraph State에 보존한 뒤 Interrupt
4. React 승인 큐는 approval_request 조회
5. POST /approvals/{approval_id}/decision 호출
   - PENDING이 아니면 409 Conflict, 처리하지 않음
6. [트랜잭션] approval_request 상태 변경(decided_by·decision_comment 저장)
   + agent_run: WAITING_APPROVAL → RUNNING
   + 기존 action_history를 APPROVED/WAITING 또는 REJECTED/CANCELED로 갱신
   + audit_log(APPROVAL_DECIDED) 기록
7. 트랜잭션 커밋
8. approval_decision과 action_id를 LangGraph State에 주입한 뒤
   저장된 thread_id로 재개한다
   (graph.update_state(config, {...}) 후 graph.invoke(None, config))
   - 승인 분기: 재개된 그래프 안에서 State의 action_id로
     send_action(action_id, agent_run_id) 호출
   - 반려 분기: 전송 없이 그래프 종료
9. send_action은 action_id를 멱등성 키로 사용해 n8n 전송,
   결과를 action_history.send_status에 기록(SENT/FAILED)
10. 정상 종료 시 agent_run=COMPLETED, 재개·Tool 실패 시 agent_run=FAILED
    (종료 시 agent_run.ended_at=now()와 latency_ms 필수 기록. latency_ms는 LLM·Tool·코드 처리시간 합계이며 HITL 사람 대기시간은 제외)
11. 전송 결과를 audit_log(ACTION_SENT/ACTION_SEND_FAILED)에 기록
```

비승인 조치는 다음과 같이 처리한다.

- **MONITOR·NOTIFY·LOT_HOLD**: `approval_status='AUTO'`, `approved_by='system'`, `approved_at=created_at`인 `action_history`를 먼저 생성(`send_status='WAITING'`)한 뒤 `send_action(action_id, agent_run_id)`을 호출한다.
- **EQP_HOLD 승인**: 조치 결정 시 생성한 기존 행을 `APPROVED/WAITING`으로 갱신하고 승인자의 `decided_by/decided_at`을 `approved_by/approved_at`에 동기화한 뒤 LangGraph 재개 후 `send_action()`을 호출한다.
- **EQP_HOLD 반려**: 기존 행을 `REJECTED/CANCELED`로 갱신하고 `approved_by/approved_at`은 NULL로 유지하며 `send_action()`을 호출하지 않는다.

초기 데이터의 `action_history` 승인 대기 2건(`approval_status='PENDING'`, `send_status='WAITING'`)은 초기 승인 화면 Mock 개발에 사용할 수 있다. 하지만 최종 LangGraph HITL 승인 큐는 `approval_request`를 단일 기준으로 사용한다.

### 11.4 `send_status` 상태 전이

`action_history.send_status`는 조치 등급과 처리 단계에 따라 아래 값만 갖는다(실제 배포 데이터 기준: `SENT`, `WAITING`). 이 표를 기준으로 삼아 5.2의 `send_action()` 멱등 로직을 구현한다.

- `MONITOR`·`NOTIFY`·`LOT_HOLD`: 생성 시 `WAITING`
- `EQP_HOLD`: 조치 결정 시 `approval_status=PENDING`, `send_status=WAITING`으로 선생성
- 승인된 `EQP_HOLD`: 기존 행 `PENDING/WAITING → APPROVED/WAITING`
- 반려된 `EQP_HOLD`: 기존 행 `PENDING/WAITING → REJECTED/CANCELED`
- 최초 전송: `WAITING → SENDING → SENT` 또는 `WAITING → SENDING → FAILED`
- 반려 전이: `WAITING → CANCELED` (`send_action()` 미호출)
- 재시도: 제한 횟수 안에서 `FAILED → SENDING`
- `SENDING`·`SENT`·`CANCELED` 상태에서는 새로 전송하지 않는다

## 12. 평가 분담

평가는 D 한 명이 전체를 담당하지 않는다. 해당 기능을 구현한 담당자가 자신의 기능을 평가한다.

| 평가 영역 | 담당 | 주요 기준 |
|---|---|---|
| 요약·규칙·이상감지 | A | 재계산 일치율, 알람 51건 재현(규칙 판정 기준), 모델 지표(ML 1차: `fault_code != 'NRM'` 기준 정밀도·재현율 / 2차 보조: `metrology.judgement` PASS/FAIL 연관성 — 40건뿐이므로 보조 지표, 규칙과 분리 평가) |
| Neo4j·RAG 검색 | B | 관계 정답률, Recall@K·MRR 등 검색 지표 |
| Fault 분류·Tool·HITL | C | 런타임은 incident당 agent_run 1건이며, 동일 분류 로직은 별도 오프라인 `fdc_alarm` 51건으로 전수 평가(FOC 22 / RFM 15 / MFD 14). Accuracy·클래스별 P·R·F1·Macro-F1·혼동행렬, Accuracy·Macro-F1 각 0.80 비강제 목표. `TMD`는 TROUBLE 3.4 합성 fixture로 검증. Tool 계약 준수, 8회 총예산·최초 전송 예약·HITL 누적, 승인 흐름, `send_action` 멱등성, 자율성 Level 1·2 완료율·호출 수·HITL 대기 제외 지연시간·토큰 비교 |
| Text2SQL·통계·차트 | D | 운영 최신 DB 결과와 전용 화면 정합, 평가 DB 격리, 골드 12건 중 10건 이상 실행 정확도, 안전 방어 6종, 교정 재생성 최대 1회, 차트 호환성 |
| 전체 E2E | 4명 공동 | 골든 시나리오 4건(15.1) 전체 통과 |

변경 효과:
- 특정 팀원에게 평가 업무가 집중되지 않음
- 구현 내용과 평가 기준의 불일치 방지
- 각자 포트폴리오에 정량 평가 결과 포함
- 최종 결과 보고서의 담당 근거 명확화

## 13. 개발 순서

각 기능은 화면을 모두 만든 뒤 Backend를 연결하는 방식으로 개발하지 않는다.

```text
1. Pydantic 요청·응답 스키마 정의
2. 예제 JSON 작성
3. React 화면을 예제 JSON으로 생성
4. FastAPI 엔드포인트 구현
5. 실제 API 연결
6. Loading·Error·Empty 상태 처리
7. Backend·Frontend 통합 테스트
8. 기능 평가
```

이 방식을 사용하면 AI 도구로 React 초안을 빠르게 만들면서 API 변경에 따른 재작업을 줄일 수 있다.

## 14. 주차별 일정

| 주차 | A | B | C | D | 공통 체크포인트 |
|---|---|---|---|---|---|
| 1주차 | 요약·규칙 POC, 운영 대시보드 Mock | Cypher·RAG POC, 관계·근거 Mock | State·Node 설계, 승인 화면 Mock | 분석 출력 스키마, 질의 화면 Mock | Tool·API·감사 이벤트 계약 동결, KOSA 지급 LLM API 연결·구조화 출력·API 키 관리 확인 |
| 2주차 | 알람·trace API·화면 연결 | 관계·문서 Tool·화면 연결 | LangGraph 기본 흐름·분류 | Text2SQL·표 연결 | 모듈별 API 통합 확인 |
| 3주차 | 모델 baseline·Tool·차트 | 검색 튜닝·근거 화면 | Tool 통합·체크포인트 | SQL 안전장치·동적 차트 | Agent 입력 Tool 계약 회귀 테스트 |
| 4주차 | 운영 대시보드 실제 데이터 완성 | Agent 근거 통합 지원 | HITL 중단·승인·재개, `send_action` 멱등 처리, 배치 트리거, 최소 n8n Webhook | 감사로그 조회·화면 | 골든 시나리오 2번(EQP_HOLD 승인) 성공 |
| 5주차 | Detection 평가·개선 | RAG 평가·개선 | n8n 전송 안정화·에러 처리·Agent 평가, `AGENT_AUTONOMY_LEVEL=1` 폴백 검증 | Text2SQL·통계·차트 평가 | 골든 시나리오 4건 전체 검증, 전체 오류·성능 개선 |
| 6주차 | 안정화·문서화 | 안정화·문서화 | 안정화·문서화 | 안정화·문서화 | 최종 통합·시연·발표 |

**공식 산출물 제출 일정(멘토 원안, 프로젝트 기간 7/30~9/11)**

- 8/7 (1주차 말): 요구사항 정의서 — 1주차 계약 동결(Tool 스키마·API·감사 이벤트)이 그대로 재료가 된다
- 8/14 (2주차 말): 시스템 설계서
- 9/4 (5주차 말): 소스코드 (Git 저장소)
- 9/8 (6주차 초): 테스트·평가 결과서
- 9/11 (종료): 사용설명서·완료보고서·실행 가이드 — 멘토 원안상 발표자료로 대체 가능

### 14.1 4주차 핵심 목표

```text
알람 목록에서 알람 선택
→ 센서 요약·trace 확인
→ Agent 분석 실행
→ Neo4j·문서 근거 조회
→ Fault·원인·조치 표시
→ EQP_HOLD 승인 요청
→ POST /approvals/{approval_id}/decision (승인 또는 반려)
→ 승인 시 저장된 thread_id로 LangGraph 재개 → send_action(action_id, agent_run_id)
→ 최소 n8n Webhook 전송
→ action_history·audit_log 기록
```

### 14.2 5주차 n8n 고도화

4주차에 조치 전송의 최소 흐름을 완성하고 5주차에는 다음을 보완한다.

- MES·메일 모의 전송 포맷
- 전송 실패 처리
- 제한된 재시도(같은 `action_id`로 재시도해도 중복 전송되지 않음)
- 타임아웃
- `send_status`·`sent_at` 업데이트

## 15. 우선순위

### 15.1 1순위 - 반드시 완성

핵심 흐름 자체는 다음과 같다.

```text
알람 → FDC 요약 → 관계·문서 근거 → Agent 분류·원인·조치 → EQP_HOLD 승인 → n8n 전송 → 감사로그
```

이 흐름을 검증하는 골든 시나리오는 1건이 아니라 아래 4건으로 고정한다.

1. `MONITOR`·`NOTIFY`·`LOT_HOLD` 중 하나의 자동전송 성공 (승인 불필요 경로)
2. `EQP_HOLD` 승인 후 n8n 전송 성공
3. `EQP_HOLD` 반려 후 미전송
4. Tool 또는 n8n 실패 후 오류·감사로그 기록

4주차 핵심 목표(14.1)는 이 중 시나리오 2번을 기준으로 하고, 나머지 3건(1·3·4)은 5~6주차 안정화 기간에 함께 검증한다.

### 15.2 2순위 - 프로젝트 필수 범위

- 이상감지 모델과 평가
- 운영 대시보드
- 센서 trace
- 관계 그래프·문서 근거
- Text2SQL
- 동적 통계·차트
- 기능별 평가

### 15.3 도전 과제 — 핵심 완료 후 선택

- Tool의 MCP 서버 wrapping
- Text2SQL Tool의 LangGraph 연결
- `AGENT_AUTONOMY_LEVEL=3` 완전 자율 ReAct (멘토 원안이 명시한 도전 과제. Level 2가 안정화된 뒤에만 시도)

MCP wrapping을 포함한 도전 과제는 핵심 기능과 평가가 완료된 5주차 이후에만 진행한다.

### 15.4 범위 밖·후속 확장

- ML 예측 기반 `fdc_alarm` 추가 생성
- 승인 요청 자동 만료 스케줄러
- 실시간 WebSocket
- 멀티에이전트
- 사용자별 대시보드
- 복잡한 인증·권한
- 모바일 최적화
- 화려한 애니메이션
- 클라우드·Kubernetes

위 항목은 초기 프로젝트 완료 범위와 일정에 포함하지 않는다. 최종 필수·도전 과제가 모두 끝난 뒤 별도 후속 프로젝트로만 검토한다.

## 16. React·Docker 처리 기준과 공통 코드 관리

### 16.1 React

- React 공통 초안은 대혁님이 AI 코딩 도구로 생성한다.
- 이후 각 담당자는 자신의 Backend와 React 기능을 직접 연결한다.
- API 응답 타입은 FastAPI Pydantic/OpenAPI 계약을 기준으로 맞춘다.
- 각 기능 담당자는 Loading·Error·Empty 상태까지 처리한다.
- 최종 화면 통일은 4명이 공통 디자인 규칙에 맞춰 확인한다.

### 16.2 Docker

이미 완료된 항목:
- PostgreSQL
- Neo4j
- n8n
- 데이터·관계·문서 적재

**현재 배포된 `docker-compose.yml`에는 PostgreSQL·Neo4j·n8n만 있다.** Streamlit용 `app` 서비스는 통째로 주석 처리돼 있고, FastAPI·React 서비스는 없다. 배포 원본 `requirements.txt`에도 `fastapi`·`uvicorn`이 없다(Streamlit·langgraph·sqlglot 등만 있음). React+FastAPI 전환에 따라 단일 `bistel-final` 모노레포 루트에 통합 구성을 만든다.

- `backend/requirements.txt`에 `fastapi`, `uvicorn` 추가
- `backend/Dockerfile`·`frontend/Dockerfile` 작성
- 모노레포 루트 `docker-compose.yml`에 FastAPI(API 포트)·React(Web 포트) 서비스 추가
- API Base URL·CORS 설정
- KOSA 지급 LLM API의 provider·model·base URL을 `.env`로 주입(키 커밋 금지, Ollama 서비스 추가 불필요)
- n8n 서비스 주석("승인된 건만 발송")을 실제 동작(자동조치 + 승인된 EQP_HOLD)에 맞게 수정(8번 참고)
- n8n은 검증된 `n8nio/n8n:2.32.7`과 digest `sha256:882b126a8ddd0646e7d17ec47630e7704615e4647f3363471859fddc3f8946e2`를 기준으로 고정한다
- PostgreSQL/pgvector·Neo4j·n8n·Backend·Frontend 전체 이미지의 최종 태그·digest를 시스템 설계서와 루트 Compose에 기록한다. Frontend는 Node 버전과 `frontend/package-lock.json`을 고정하고 `npm ci`로 설치한다
- PostgreSQL Checkpoint 테이블은 배포 `01_schema.sql`을 수정하지 않고 별도 1회 초기화 절차로 생성한다. `PostgresSaver.setup()`을 애플리케이션 시작 때마다 실행하지 않으며 공용 DB 적용 전 팀 공유·멘토 확인을 거친다
- 배포 원본 업무 스키마는 수정하지 않되, Agent 런타임용 `agent_run_alarm`·`action_delivery`·컬럼·인덱스와 Checkpoint는 C가 초안을 맡고 D는 Text2SQL 로그·계정 권한을 검토한다. 실제 migration·bootstrap 반영은 공통 통합 변경으로 4명이 함께 리뷰한다
- 기존 공용 교육장 서버의 배포 기본 DB 자격증명·과도한 readonly 권한도 그대로 사용하지 않는다. 원본 `01_schema.sql`은 수정하지 않고 팀·멘토님께 변경 시각과 짧은 재접속 구간을 공유한 뒤, 공통 통합 담당(C)이 `kosa_app`·`kosa_readonly`·`kosa_query_logger`·`kosa_n8n_delivery`의 1회 최소권한 전환과 기존 project role 세션·pool 재시작을 수행한다. D는 readonly 16개 테이블 SELECT·logger 고정 INSERT와 각 계정의 쓰기/비허용 접근 거부를 검증한다. 비밀번호 원문은 Git·PR·문서·명령행·로그에 남기지 않으며 세부 절차는 시스템설계서 v1.10 13.2.2를 따른다

### 16.3 공통 코드 관리

4명이 동등한 역할이어도 아래 공통 코드는 관리 기준이 필요하다.

- `backend/app/main.py`, DB 세션, 환경변수, 공통 예외 응답
- `frontend/src`의 React 라우팅, API Client, 공통 레이아웃
- 루트 `docker-compose.yml` 최종 통합(FastAPI·React 서비스 추가 포함)
- 루트 `docs/`의 요구사항·시스템 설계·API 계약·테스트·Trouble Shooting
- `backend/migrations/`와 DB 계정·권한 bootstrap 계약(C: Agent 런타임, D: Text2SQL 로그, 전원: 통합 리뷰)

별도 직무로 만들 필요는 없고 다음처럼 운영한다.

- 공통 기반 코드는 1주차에 4명이 함께 확정한다.
- 공통 코드 변경은 최소 1명의 리뷰를 받는다.
- Backend API 계약이 바뀌는 PR은 같은 PR에서 Frontend 타입·연결 코드와 `docs/` 계약 문서를 함께 갱신한다.
- **대혁님은 최종 통합 시 release captain 역할을 맡는다.** 다만 통합 중 발견된 기능별 오류는 별도 역할로 떠안지 않고 해당 A·B·C·D 담당자가 직접 수정한다.

### 16.4 외부 서비스 장애 대응

- 외부 서비스(PostgreSQL·Neo4j·n8n) 장애 중에도 FastAPI 프로세스는 종료되지 않아야 한다
- 장애가 발생한 기능의 API만 503으로 격리하고 나머지 기능은 정상 동작한다
- 장애 주입 검증은 4명 공동 책임이며, 기능별 장애 원인은 해당 담당자가 수정한다

## 17. 채용요건 대비 경험

| 역할 | Python | FastAPI | React | LangGraph 접점 | Function Calling | 평가 경험 |
|---|---|---|---|---|---|---|
| A | O | O | O | Tool 연동 | FDC 요약 Tool | 규칙·ML(분리 평가) |
| B | O | O | O | Tool 연동 | 관계·문서 Tool | RAG |
| C | O | O | O | 직접 설계 | 조치 Tool(멱등)·오케스트레이션 | Agent·HITL |
| D | O | O | O | 확장 연결 가능 | Text2SQL 분석 Tool | SQL·통계·차트 |

역할별 연결 직무:
- A: ML Engineer, Data Engineer, AI Full-stack Engineer
- B: RAG Engineer, Knowledge Graph Engineer, AI Search Engineer
- C: AI Agent Engineer, LLM Backend Engineer, Workflow Automation Engineer
- D: AI Backend Engineer, AI Full-stack Engineer, Data Analytics Engineer

각 담당자는 최종 발표와 면접에서 다음 흐름으로 자신의 기능을 설명할 수 있어야 한다.

```text
어떤 데이터를 사용했는가
→ 어떤 로직을 구현했는가
→ 어떤 Tool·API를 설계했는가
→ React에서 어떻게 사용되는가
→ 어떤 기준으로 테스트·평가했는가
```

## 18. 역할 확정·협업 규칙

- 역할은 A 신동원, B 강연권, C 방대혁, D 천승현으로 확정한다.
- C가 가장 높은 난이도·강도를 맡는 현재 분배를 팀 합의로 유지한다. React는 방대혁이 공통 초안만 만들고, 이후 실제 화면 구현·API 연결·기능 검증은 각 담당자가 자기 파트를 직접 수행한다.
- 각 기능에는 주담당 1명과 코드 리뷰 담당 1명을 지정한다.
- Tool·API 계약 변경은 PR과 테스트 변경으로 공유한다.
- 공통 컴포넌트·공통 코드 변경은 최소 1명의 리뷰를 받는다.
- 최종 E2E 골든 시나리오 4건(15.1)은 4명이 공동 책임진다.
- **공통 산출물 책임**: 각 담당자는 자신의 도메인에 대한 Tool·API 명세, 설계 내용, 테스트·평가 결과, Trouble Shooting을 직접 작성한다. 최종 요구사항 정의서·시스템 설계서·테스트 평가 결과서와 종료 산출물(사용설명서·완료보고서·실행 가이드 — 멘토 원안상 발표자료로 대체 가능)은 4명이 공동으로 통합한다.

## 19. 최종 변경사항 요약 (v9.1 → v9.6)

- Tool 반환 형식을 v9.1의 자체 제작 `{data, error, meta}` 래퍼에서 **멘토 개발 가이드 원안의 `{ok, ...필드..., reason}` 형식**으로 정정(9.2). Agent Tool의 `latency_ms`·호출 상태는 `agent_tool_call`, 독립 D Analytics 호출은 `nl_query_log`에 별도 기록
- C의 책임에 멘토 원안의 구체적 안전장치를 명시: `AGENT_AUTONOMY_LEVEL`(1/2/3, 기본 2), `AGENT_MAX_TOOL_CALLS=8`, `AGENT_MAX_RETRY=3`
- `agent_run.llm_model`·`latency_ms`는 성공·실패 모두 필수, `input_tokens`·`output_tokens`는 제공자 반환 시 기록하도록 C의 책임과 완료 기준에 명시
- A의 `ANOMALY_SCORE_THRESHOLD=0.62`(이상 여부)와 C의 `SEVERITY_HIGH_THRESHOLD=0.80`·`HITL_REQUIRED_SEVERITY=HIGH`(심각도·승인 게이트)를 서로 다른 임계값으로 명확히 구분하고, 임계값을 승인 여부에 직접 연결하지 않고 `decide_action()` 규칙 함수를 거치도록 명시
- 9.3의 배포 Ollama 복사 예시를 제거하고 KOSA 지급 API용 placeholder로 교체. 2026.07 합의에 따라 KOSA API를 최종 LLM으로 확정하고 임베딩은 기존 `bge-m3`/1024차원 유지
- D에 `kosa_readonly` 계정명과 `sqlglot` 파서를 구체적으로 명시
- 15.3 도전 과제에 `AGENT_AUTONOMY_LEVEL=3`(완전 자율 ReAct)을 멘토 원안의 명시적 도전 과제로 별도 추가
- 16.2에 현재 Docker Compose가 PostgreSQL·Neo4j·n8n까지만 실행하며 FastAPI·React 서비스와 `fastapi`/`uvicorn` 의존성이 없다는 점, KOSA 지급 LLM API를 `.env`로 연결한다는 점을 명시
- 8번에 배포된 `docker-compose.yml`의 n8n 서비스 주석("승인된 건만 발송")이 자동조치 경로를 빠뜨려 실제 동작과 다르다는 점과 수정 문구를 명시
- 서두에 멘토 원안이 React 풀스택을 "범위 밖"으로 명시했다는 점과 2026.07.31 멘토링에서 FastAPI+React 전환이 확정됐음을 명시
- 10.2에 멘토 원안의 최소 5개 이벤트(감지·판단·승인요청·승인·전송)와 v9.2의 9개 이벤트 매핑을 추가
- A의 FDC Tool 반환에 `anomaly_score`·`anomaly_threshold`·`is_anomaly`를 원안 확장으로 추가하고, 점수는 0~1 정규화·높을수록 이상으로 A·C 공통 정의(0.62와 0.80을 같은 축에서 비교 가능하게)
- C의 `decide_action()`에 원본 `TROUBLE_FDC_FaultGuide.md` 5장의 조치 결정표(OOC→MONITOR, OOS 1~2→NOTIFY, OOS 3+→LOT_HOLD, R03_CONSEC→EQP_HOLD)와 상향·하향 조건을 명시, `0.80`은 보조 위험 신호로만 사용
- 승인 재개 시 LangGraph State에 `approval_id`·`approval_decision`·`action_id`를 주입한 뒤 재개하도록 11.3 수정(`action_history`에 `agent_run_id` 컬럼이 없어 State 주입이 필수), 승인 API 본문에 `decided_by`·`decision_comment` 추가(스키마 컬럼 그대로)
- `send_action.sent`를 "최종 전송 상태"로 재정의(이미 SENT면 `sent=true` + reason으로 중복 구분) — React가 중복 호출을 실패로 오해하지 않게
- A의 ML 평가를 1차(`fault_code != 'NRM'`)·2차 보조(`metrology.judgement` PASS/FAIL 연관성, 40건이라 보조 지표)로 분리 — 원안 "계측 결과 대비" 요구 반영
- 9.2에 Tool 타임아웃 계약 복구(`{"ok": false, "reason": "TIMEOUT: ..."}` + `agent_tool_call.status='TIMEOUT'` + `AGENT_MAX_RETRY` 연계)
- 18번에 공통 산출물 책임 추가(도메인별 명세·평가·Trouble Shooting은 각자, 최종 문서 6종은 공동 통합)
- 5.1 결정표에 판정 우선순위(R03_CONSEC → OOS 3+ → OOS 1~2 → OOC) 명시 — 조건 겹침으로 인한 오판정 방지
- 5.3에 `HITL_REQUIRED_SEVERITY=HIGH` 규칙(severity가 HIGH면 Interrupt, 1차 구현은 EQP_HOLD=HIGH 매핑) 명시
- 11.2의 action_history 생성 시점을 승인 결정 시점에서 **조치 결정 시점**으로 정정. 자동 조치는 AUTO/WAITING·system 승인자, EQP_HOLD는 PENDING/WAITING·승인자 NULL로 선생성하고 승인·반려 시 기존 행만 갱신. 승인 시 `decided_by/decided_at`을 `approved_by/approved_at`에 동기화하고 반려 시 승인자 필드는 NULL 유지
- 14번에 공식 산출물 제출 일정(8/7·8/14·9/4·9/8·9/11) 추가
- A의 anomaly_score에 재현성 규칙(정규화 공식·모델 버전 기록, 평가 데이터로 정규화 금지) 추가
- D Tool 출력에 공통 계약(`ok`·`reason`) 적용 — 분석 필드는 자체 확장이되 성공·오류 계약은 다른 Tool과 통일
- "조치하지 않는다"의 DB 처리 확정 — `NO_ACTION` 코드를 만들지 않고 `recommended_action=NULL` + `action_history` 생략 + 사유는 `action_reason`·감사로그 기록, 하향 최저 조치는 `MONITOR`
- 승인 상태 변경을 조건부 갱신(`UPDATE ... WHERE status='PENDING' RETURNING`)으로 명시 — SELECT 후 UPDATE 방식의 동시성 허점 차단
- 9/11 종료 산출물 표현을 원안대로 정정("발표자료로 대체 가능")
- 11.3 종료 단계에 `agent_run.ended_at`·`latency_ms` 기록 추가
- 런타임은 incident당 agent_run 1건으로 확정하고, 동일 분류 로직의 오프라인 fdc_alarm 51개 행(FOC 22 / RFM 15 / MFD 14)을 C 평가 집합으로 분리하여 Accuracy·Macro-F1 0.80 비강제 목표를 추가. 기존 lot_history 표본 20/15/15와 구분
- D의 잘못된 SQL 제한 재시도를 구체화: 읽기 의도의 구문·스키마·컬럼 오류만 1회 교정 재생성, 정책 위반은 즉시 거부, AST 재귀 검증
- `(lot_id, chamber_id)` incident 전체 집계·DISTINCT wafer_no 계수·조치 1건 원칙, 전체 배치 순서와 무관한 ALM-0031 상류 근거, 즉시 재배치 신규 실행 0건 기준을 C 책임에 추가
- 운영 대시보드 고정 KPI·필터는 A, 동적 자연어 분석은 D로 유지하고 외부 서비스 장애 시 기능별 격리 기준과 전체 이미지·Node 재현성 기준을 공통 통합에 추가
- 대시보드의 “당일”을 조회 기준일로 정정하고 date 미지정 시 최신 데이터 일자, AREA만 지정 시 해당 AREA 최신 일자를 사용하도록 확정. 배포 기본 조회일 `2026-06-04`, 최초 알람 6건, API `reference_date` 반환과 `WAITING → CANCELED` 반려 전이를 명시
- 대시보드 KPI 산식·챔버 상태·최근 5건·PASS율 null/N/A·AREA 변경 시 자동/수동 기준일 동작을 확정하고 배포 기대값(6건·OOS 6·OOC 0·PASS 70.0%) 추가
- IsolationForest는 fdc_alarm을 추가 생성하지 않는 것으로 확정하여 규칙 알람 51건을 유지하고, ML 알람 생성은 범위 밖으로 이동
- 승인 자동 만료 스케줄러는 범위 밖으로 두고 EXPIRED는 409·관련 데이터 무변경 터미널 fixture로만 지원. Ollama 복사 예시는 KOSA API placeholder로 교체
- 자동 배치를 incident당 agent_run 1건으로 확정하고, fdc_alarm 51건 분류는 동일 로직의 오프라인 평가로 분리. 대표 alarm_id·포함 alarm_ids 추적을 설계서 항목으로 명시
- Agent 흐름의 A·B·C Tool은 agent_tool_call, 독립 D Analytics Tool은 nl_query_log에 기록하도록 스키마 정합화
- 원안 Agent Tool 4종과 독립 Analytics Tool 1종을 구분해 총 5종 표기의 사유를 명시하고, D Tool을 LangGraph 호출 예산에서 제외
- C의 Tool 8회 예산에서 최초 `send_action` 1회를 예약하고 HITL 재개 전후 호출 수를 누적하도록 완료 기준을 보강
- 운영 Text2SQL은 최신 `kosa_agent`, 골드 평가는 기준 `kosa_text2sql`을 사용하도록 D 책임과 로그 경로를 분리
- 기존 공용 교육장 서버의 기본 자격증명·전체 SELECT 권한을 원본 스키마 무수정 최소권한 role 4종 전환 대상으로 명시(C 실행·pool 재시작, D 권한 검증, 전원 리뷰)
- PostgreSQL Checkpoint 테이블을 별도 1회 초기화 작업으로 관리하고, llm_model·latency_ms 필수 기록 및 HITL 사람 대기시간 제외 규칙 추가
- 단일 `bistel-final` 모노레포 아래 `backend/`·`frontend/`·`docs/`·루트 Compose 구조를 확정하고 기능 폴더별 README 의무를 제거
- 도전 과제(MCP·Level 3 등)와 범위 밖 후속 확장(ML 알람·승인 만료 등)을 별도 절로 분리
- A·B·C·D 역할 소유권, 각 담당자의 React 실제 연결, 대혁님의 React 공통 초안·release captain 역할은 변경하지 않음. 기능 동작·수용 기준은 요구사항정의서 v1.8을 우선 적용

## 20. v9.6 정합 보정

- 기능 동작·수용 기준의 우선 문서를 `요구사항정의서_v1_9_최종.md`, 구현 계약을 `시스템설계서_v1_10_최종.md`로 갱신했다.
- 최종 UI를 8개 업무 화면으로 확정했다. A는 알람 대시보드·알람·Trace, B는 관계·문서 근거, C는 조치 목록·Agent 실행 근거·승인, D는 자연어 분석·감사로그를 맡는다.
- A의 Trace API를 `GET /traces/catalog`와 `POST /traces/search`로 교체하고, C의 목록 API `GET /agent/runs`·`GET /actions`를 명시했다.
- 알람 대시보드에서 계측 PASS율·챔버 상태 카드를 제외하고 일자별 추이·파라미터 상위 5개·설비별 건수·승인 대기 목록·최근 알람 5건을 제공하도록 책임을 정렬했다.
- `/dashboard`와 `/analytics`는 별도 화면으로 유지하며 공통 내비게이션으로 연결한다. 기존의 단일 탭 통합 제안은 적용하지 않는다.
- 역할 소유권과 난이도·강도는 변경하지 않는다. C가 가장 높은 난이도를 맡고, React 공통 골격은 방대혁이 관리하되 각 기능의 실제 연결·검증은 담당자가 수행한다.
