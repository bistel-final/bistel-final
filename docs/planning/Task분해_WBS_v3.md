# 팀 전체 Task 분해 (WBS) v3

> [!CAUTION]
> **FINAL-DOC — LEGACY ARCHIVE.** 이 문서는 최종 `project.zip` 이전 epoch의 WBS 이력이며
> 신규 구현·수용 기준·완료 근거로 사용하지 않는다. 현재 기준은
> [최종 데이터 기준표](../reference/mentor-final-20260818/README.md),
> [요구사항 v2.1](../specifications/요구사항정의서_v2_1_작업본.md),
> [시스템설계 v2.1](../specifications/시스템설계서_v2_1_작업본.md),
> [역할분담 v10.1](../specifications/FDC_프로젝트_역할분담_v10_1_작업본.md),
> [API v3](../deliverables/api/API명세서_v3_작업본.md)이다. 이 문서의 Task ID·공수·선행관계·
> 완료 표시는 새 WBS v5로 승계하지 않는다.

> 문서 성격: 요구사항 v1.9, 시스템설계서 v1.10, 역할분담 v9.6 기준 정합본
> 작성 기준일: 2026-08-12
> Git 기준본: Task ID·범위·선행관계·완료 기준을 추적한다. 담당자·진행 상태·일정·블로커는 Notion Task DB에서 실시간 관리하며 상태만 바뀌는 경우 이 파일을 수정하지 않는다.
> 동기화 규칙: Task ID·범위·선행관계·완료 기준이 바뀌면 버전을 올려 관련 코드·문서와 같은 PR에서 갱신한다.
> 작성 단위: 구현과 검증이 가능한 0.5h-2h 작업 단위
> 상태 원칙: 문서 비교만으로 완료를 확정하지 않으며 기존 완료 표시는 검증 필요로 전환
> 채택 범위 예상 공수: 202.0h, 공통 작업은 4명 공동
> 도전 과제: 8.5h 별도, 채택 범위 완료 후 진행

---

교차 검토 결과 반영 최종 수정본. `Task분해_WBS_v2.md`를 현재 저장소의 확정 문서(요구사항정의서 v1.9·시스템설계서 v1.10·역할분담 v9.6)와 대조해 담당별 Task·완료 기준·요구사항 ID·선행 의존성·예상 시간을 다시 배치한 전체 수정본이다.

## 1. 문서 목적과 적용 원칙

문서 우선순위는 **요구사항정의서 v1.9 → 시스템설계서 v1.10 → 역할분담 v9.6 → `docs/ai-context` → 코드** 순이다. Backend schema와 Tool contract는 확정 문서와 일치해야 하는 구현 산출물이지 상위 요구사항과 동급의 근거가 아니다.

- 배포 원본 SQL, CSV, Cypher, Markdown은 수정하지 않는다.
- 공용 교육장 서버의 업무 데이터는 읽기 전용으로 검증한다. 관리자 승인 credential 전환은 CM-1.5의 제한된 절차로만 수행한다.
- 최초 데이터 적재는 A/B 개인 업무가 아니라 공통 fresh Compose bootstrap에서만 수행한다.
- 모든 비동기 화면은 Loading·Error·Empty·Success를 구분한다.
- 완료 표시는 테스트 명령·결과·요구사항 ID·산출물 링크가 모두 있을 때만 사용한다.
- E2E는 격리 DB에서만 수행하며 공용 PostgreSQL·Neo4j·n8n 컨테이너를 중지하지 않는다.
- P0는 계약·보안·데이터 무결성 선행 작업이고 P1은 필수 기능, P2는 권장 또는 도전 범위다.

## 2. 공수와 담당 요약

| 영역 | 담당 | 채택 범위 공수 | 핵심 트랙 |
|---|---|---|---|
| A Detection | 신동원 | 41.5h | 비파괴 요약/룰 재현 → IsolationForest → Tool/API → Detection 화면 |
| B Knowledge | 강연권 | 22.5h | 기반 검증 → Neo4j/pgvector 조회 → Tool/API → Knowledge 화면/평가 |
| C Agent/HITL | 방대혁 | 71.0h | runtime 기반 → incident → graph/결정 → HITL/n8n → 복구/E2E/평가 |
| D Analytics | 천승현 | 37.5h | 분리 pool factory → SQL 12단계 → Plan/질의 → 감사/평가 → Analytics 화면 |
| Common Integration | 4명 공동, 통합 관리 C | 29.5h | 문서 정합 → preflight → fresh Compose/공용 credential → 배포 → 격리 E2E |
| **합계** | | **202.0h** | 도전 과제 8.5h 제외 |

C 영역의 핵심 경로 공수가 가장 크다. CM-1·CM-2·CM-3은 통합 관리만 C가 맡고 구현과 리뷰는 4명이 분담한다. 202.0h에는 프로젝트가 채택한 권장 `validate` API가 포함되며, 별도 도전 과제는 포함되지 않는다.

## 3. v2에서 수정한 핵심 사항

| 구분 | v2 내용 | v3 반영 |
|---|---|---|
| 기반구축 | A-1a', B-1b~B-1e에 최초 적재 중복 배정 | CM-1 fresh bootstrap으로 일원화 |
| R03 | 보조 정렬 키와 재무장 조건 불충분 | 세 정렬 키, LOT 경계 유지, 2에서 3 전이 1회, 비OOS reset 명시 |
| 승인 목록 | A가 C 테이블을 직접 JOIN | C `ApprovalService` 직접 재사용, SQL/HTTP self-call 금지 |
| Migration | 신규 컬럼 6종 | 신규 컬럼 7종, unique index 5종의 유형과 적용 순서 명시 |
| Checkpoint | thread_id와 agent_run_id 동일시 | run 생성 시 독립 UUID thread_id 저장, 같은 thread 재개 |
| SENDING 복구 | 안전 재개라는 모호한 표현 | stale 기준과 delivery 존재·부재·hash 충돌 3분기 확정 |
| Agent E2E | 배포 action 10건 보존 | action_history 포함 runtime 0건, fixture 파일만 유지 |
| 자율성 Level | Level 1을 항상 승인으로 표현 | 고정 Node 순서와 근거 기반 분기, 승인 게이트 공통 |
| Text2SQL | 일부 검증 단계와 로그 3분기 | sqlglot 12단계와 QueryLog 4조합으로 확정 |
| Frontend | 3상태 | Loading·Error·Empty·Success 4상태 |
| 선행 관계 | 구현과 통합 검증이 서로 선행하는 순환 존재 | 산출물 작성 → bootstrap 적용 → 권한/통합 검증의 단방향 DAG로 수정 |
| 운영 보안 | 공용 서버 기본 credential 전환 누락 | 사전 공유, 최소권한 전환, 기존 비밀번호 로그인 실패 검증 추가 |
| 공수 표기 | 171h | 수정 Task 기준 채택 범위 202.0h, 도전 8.5h 별도 |

---

## 4. A — Detection

### A-0. 환경과 기준선

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-0.1 | **P0** 공용 데이터 검증: `verify_source_data.py --profile runtime`으로 기준/생산 데이터의 건수와 canonical hash를 읽기 전용 검증. 완료: trace 4,800·summary 1,600·alarm 51 등 manifest 일치, INSERT/UPDATE 0회 | FR-A-01, FR-A-02 / CM-0.4, 공용 DB 접근 | 0.5h |
| A-0.2 | **P0** 판정 기준선 검증: 센서 8종 한계선과 명시적 `upper_only` 규칙 확인. 완료: ET_REFL 하한 판정/화면 표시 제외, 원본 스키마 변경 0건 | FR-A-01, FR-A-02, FR-A-07 / 설계 5.1 | 0.5h |

### A-1. 요약 재계산

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-1.1 | **P0** 그룹 집계: `(lot_hist_id, sensor_id, recipe_step_no)`별 mean·std ddof=1·min·max·point count 계산. 완료: 1,600 PK와 숫자 오차 0.0001 이하 | FR-A-01 / A-0 | 1.5h |
| A-1.2 | **P0** OOC/OOS point 판정: 센서 한계선과 `upper_only` 메타데이터 적용. 완료: point count 전량 기준값 일치 | FR-A-01 / A-1.1 | 1.0h |
| A-1.3 | **P0** summary judgement: OOS → OOC → IN_CONTROL 우선순위만 구현. 완료: judgement 전량 일치, 알람 발행 로직과 분리 | FR-A-01 / A-1.2 | 0.5h |
| A-1.4 | **P0** 비파괴 재현 검사: 메모리 또는 임시 스키마 후보와 배포 summary를 PK 집합으로 비교. 완료: diff 0건, 동일 입력 재실행 동일, 공용 DB 쓰기 0회 | FR-A-01 / 설계 5.1 | 1.5h |

### A-2. 알람 규칙 엔진

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-2.1 | **P0** R01_OOS: `oos_point_cnt >= 1`. 완료: 34건 재현 | FR-A-02 / A-1 | 1.0h |
| A-2.2 | **P0** R02_OOC: OOS 0이고 OOC point 2 이상. 완료: 14건 재현 | FR-A-02 / A-1 | 1.0h |
| A-2.3 | **P0** R03_CONSEC: key는 chamber/sensor/recipe step, 정렬은 chamber wafer 누적/track in/lot history ID ASC. 완료: LOT 경계에서 counter 유지, 비OOS에서 reset/re-arm, 2에서 3 전이 1회, 4장 이상 추가 없음, ALM-0008/0022/0048 위치 일치 | FR-A-02 / 설계 5.2 | 2.0h |
| A-2.4 | **P0** 발행 시각과 canonical 비교: R01/R02 `occurred_at=lot_history.track_out_at`, canonical key 순서 무관 비교. 완료: R01 34·R02 14·R03 3, 총 51이며 동일 입력 2회 후보 집합 동일, 실제 INSERT 0회 | FR-A-02 / A-2.1~A-2.3 | 1.5h |
| A-2.5 | **P1** 감지 감사 이벤트: `DETECTION_COMPLETED` append-only 기록. 완료: LOT_HIST entity, 규칙 결과와 산출된 경우 anomaly score 포함, 업무 rollback 시 감사도 rollback | 요구 11.1 / A-2.4, C-0.6; score 포함 시 A-3.3 | 0.5h |

### A-3. IsolationForest와 평가

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-3.1 | **P0** 11개 feature 추출: 설계 5.3의 feature와 coverage ratio·epsilon·그룹 중앙값 대체를 고정. 완료: 200 WAFER 고정 벡터, fault_code와 metrology 입력 누수 0건 | FR-A-03 / A-1 | 2.0h |
| A-3.2 | **P0** LOT 분리와 학습: GroupShuffleSplit 1회/test 0.3/random 42와 IF 200 trees, max_samples/contamination auto, max_features 1, bootstrap false, n_jobs -1, random 42, sklearn 1.5.2 고정. 완료: train/test LOT 중복 0건과 artifact 단위 점수 재현 | FR-A-03 / A-3.1 | 1.5h |
| A-3.3 | **P0** 점수 정규화: train p1/p99 기반 0-1 score, threshold 0.62 적용. 완료: score와 is_anomaly 일치, p1=p99이면 score 0과 경고 기록 | FR-A-04 / A-3.2 | 1.5h |
| A-3.4 | **P0** artifact bundle: joblib과 manifest에 feature 순서·중앙값·p1/p99·split LOT·버전·SHA-256 저장. 완료: lazy read-only 1회 로드, 누락/불일치 시 REST 503과 Tool `MODEL_NOT_READY` | FR-A-03, FR-A-04 / A-3.3 | 1.5h |
| A-3.5 | **P1** 모델 평가: Precision·Recall·F1과 실패 사례 기록. 완료: 0.80은 비강제 목표, 원인/개선 계획과 fixture hash 포함 artifact 생성 | FR-A-03 / A-3.4 | 1.0h |
| A-3.6 | **P1** 보조 검증: ML 실행 전후 alarm 51건 불변과 metrology PASS/FAIL 연관 지표 계산. 완료: 알람 diff 0건, metrology는 feature/정답에 미사용 | FR-A-03, FR-A-04 / A-3.5 | 1.0h |

### A-4. get_fdc_summary Tool

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-4.1 | **P1** Repository/Service: WAFER context·센서 summary·단일 anomaly score 조회. 완료: lot_hist_id당 score 정확히 1개 | FR-A-05 / A-3.4 | 1.5h |
| A-4.2 | **P0** Tool 계약: 정상·NOT_FOUND·TIMEOUT·MODEL_NOT_READY·DEPENDENCY_ERROR. 완료: 예외 미전파, 성공 reason 빈 문자열, 실패 데이터 null/빈 목록, latency/status 반환값 미포함 | FR-A-05 / 공통 Tool 계약 | 1.5h |
| A-4.3 | **P1** C wrapper 연동 계약: A는 Tool만 제공하고 C wrapper가 agent_tool_call과 latency 기록. 완료: contract fixture와 인터페이스 합의 기록 | FR-A-05, FR-C-08 / 선행 C-0.4 | 0.5h |

### A-5. 대시보드 API

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-5.1 | **P0** 기간/계층 resolver: 양쪽 지정/생략/한쪽 생략/역전/무데이터를 분리. 완료: 기본 기간 2026-06-01~06-04, 역전 422, 계층 자체 무데이터는 `date_range=[]`/`reference_date=null`, 명시 기간 무알람은 입력 기간 유지/reference null | FR-A-06 / A-0.1 | 1.5h |
| A-5.2 | **P1** KPI 집계: alarm/OOS/OOC, daily trend, TOP5, equipment counts. 완료: 51/37/14, 집계 합 일치, 결정론적 정렬, LLM 호출 0회 | FR-A-06 / A-5.1 | 1.5h |
| A-5.3 | **P1** 필터 catalog: hierarchy와 sensor catalog를 DB에서 조립. 완료: 프론트 상수 0건 | FR-A-06, FR-A-07 | 1.0h |
| A-5.4 | **P0** 승인/최근 알람: C `ApprovalService.list_pending()`을 직접 재사용하고 A SQL/HTTP self-call 금지. 완료: 전체 PENDING과 일치, 날짜/계층과 무관, requested_at/approval_id DESC, recent 5 결정론 정렬 | FR-A-06 / C ApprovalService 제공 | 1.0h |
| A-5.5 | **P1** Router/DTO 계약: `DashboardSummaryResponse`와 REST 오류 계약. 완료: 확정 계약의 200/422 contract test | FR-A-06 / A-5.1~A-5.4 | 0.5h |

### A-6. 알람·요약·Trace API

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-6.1 | **P0** 알람 목록: 8개 필터 AND, `agent_run_alarm` 경유 최신 run 선택. 완료: page 1/size 20 기본, size 1-100, occurred_at/alarm_id DESC, 복수 run/legacy null fixture 통과 | FR-A-06 / C-1 | 1.5h |
| A-6.2 | **P1** 알람 상세: 목록과 같은 `AlarmItem` 사용. 완료: 없는 ID 404와 `{code,message,details}` | FR-A-06 / A-6.1 | 0.5h |
| A-6.3 | **P1** 요약 REST: A-4 Service 재사용. 완료: 404/422/503 구분과 단일 score 검증 | FR-A-04, FR-A-06 / A-4 | 1.0h |
| A-6.4 | **P1** Trace catalog: area/equipment/sensor/recipe/lot/wafer 선택지와 limits/anomaly threshold. 완료: ET_REFL `upper_only true` | FR-A-06 / A-0.2 | 1.0h |
| A-6.5 | **P1** Trace search: 전체 body 필터, (lot_hist_id, sensor_id) series, 양끝 포함 기간. 완료: series lot history/sensor ASC, points seq ASC, total series 수 일치 | FR-A-06 / A-6.4 | 2.0h |
| A-6.6 | **P1** limits와 step stats: 요청 센서 limits와 같은 범위의 기존 fdc_summary 통계 조회. 완료: lot history/sensor/recipe step ASC 안정 정렬 | FR-A-06 / A-6.5 | 1.0h |
| A-6.7 | **P1** 입력 경계: sensor_ids 빈/중복은 422, wafer_nos 생략/빈 배열은 전체이고 중복은 422, from/to 동시 지정 시 from보다 to가 큼. 완료: 계약별 fixture | FR-A-06 / A-6.5 | 0.5h |

### A-7. Detection Frontend

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| A-7.1 | **P1** Dashboard 실연동: 최초 날짜 미전송, 서버 적용 기간 표시, 사용자 기간 유지. 완료: 실DB KPI/차트/승인 이동 | FR-A-07 / A-5, C-6 | 1.5h |
| A-7.2 | **P1** 알람 목록/상세: 최신 Agent 상태, 실행 버튼, 상세 복원. 완료: deep link와 409 메시지 구분 | FR-A-07, FR-C-13 / A-6 | 1.0h |
| A-7.3 | **P1** Trace viewer: 다중 센서/WAFER series와 한계선. 완료: `upper_only` 하한선 미렌더링 | FR-A-07 / A-6.4~A-6.7 | 1.5h |
| A-7.4 | **P1** 상태/전이: Loading·Error·Empty·Success와 알람/Trace/Agent 링크. 완료: mock 0건, 화면 이탈/복원 확인 | FR-A-07, NFR-17 / A-7.1~A-7.3 | 1.0h |

**A 합계 41.5h**

---

## 5. B — Knowledge

### B-0. 기반 데이터 읽기 전용 검증

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-0.1 | **P0** Neo4j 검증: 공용 DB에서 노드 24·관계 26·UPSTREAM_OF를 읽기 전용 확인. 완료: 재적재/삭제 0회 | FR-B-01 / CM-0.4, 공용 DB 접근 | 0.5h |
| B-0.2 | **P0** 문서/임베딩 검증기: `verify_knowledge_base.py` 구현. 완료: 문서 3·청크 39·13/12/14, 저장 content 120-1,200자와 제목 접두어 미포함, 임베딩 입력에만 문서/절 제목 접두어, NULL 0, 1024차원, 고정 질문 3개 top4 smoke | FR-B-02 / CM-0.4, 공용 DB 접근 | 1.0h |
| B-0.3 | **P0** corpus provenance: 공용 DB는 revision 미상 한계를 기록하고, fresh 적재는 모델 ID/고정 revision·원문 SHA-256·청킹 규칙·건수 manifest 생성. 완료: 실제 corpus와 manifest mismatch 시 REST 503/Tool MODEL_NOT_READY | FR-B-02, FR-B-05 / B-0.2, CM-0.5 | 1.0h |

### B-1. Neo4j 관계 조회

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-1.1 | **P0** 챔버 Cypher: parameter binding과 관계 map projection. 완료: chamber/equipment/area/step/sibling DTO 조립 | FR-B-03 / B-0.1 | 1.0h |
| B-1.2 | **P0** 상하류 Cypher: UPSTREAM_OF 양방향 조회. 완료: upstream/downstream equipment ID ASC, 경계 fixture 통과 | FR-B-03 / B-1.1 | 1.5h |
| B-1.3 | **P1** 설비 Cypher: parameter binding, chambers 포함 map projection. 완료: chamber ID ASC, 선택 관계 null/빈 목록 처리, 필수 식별자 누락은 데이터 무결성 오류 | FR-B-03 / B-1.1 | 1.0h |
| B-1.4 | **P0** Neo4j session 경계: session lifecycle과 connection exception 매핑. 완료: parameterized Cypher만 사용, dependency override 장애에서 공용 컨테이너 중지 0회 | FR-B-03, NFR-18 | 0.5h |

### B-2. 관계 API

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-2.1 | **P1** 챔버 관계 API: `/relations/chambers/{chamber_id}`. 완료: 4개 챔버와 없는 ID 404 | FR-B-06 / B-1 | 1.0h |
| B-2.2 | **P1** 설비 관계 API: `/relations/equipment/{equipment_id}`. 완료: 2개 설비와 404 | FR-B-06 / B-1 | 0.5h |
| B-2.3 | **P1** DTO/오류 계약: 정렬, extra=forbid, `{code,message,details}`. 완료: contract test | FR-B-06 / B-2.1~B-2.2 | 0.5h |

### B-3. 문서 검색

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-3.1 | **P0** embedding singleton: `EMBEDDING_MODEL_PATH` 고정 revision lazy singleton과 lock. 완료: CM-0.5 cache/manifest read-only 사용, 동시 요청 생성 1회, 런타임 다운로드 0회 | FR-B-05 / B-0.3, CM-0.5 | 1.5h |
| B-3.2 | **P0** cosine 검색: query vector 1024, score=1-distance. 완료: score DESC, doc ID ASC, chunk seq ASC 결정론 정렬 | FR-B-04 / B-3.1 | 1.5h |
| B-3.3 | **P0** 필터/top_k: model code 지정 시 COMMON 포함, top_k 1-10. 완료: PH 모델 검색에 COMMON troubleshooting 포함 | FR-B-04 / B-3.2 | 1.0h |
| B-3.4 | **P1** 빈 결과: empty corpus repository fixture. 완료: 정상 0건은 REST 200과 빈 hits, 오류 매핑은 B-4.1/B-5.2에서 검증 | FR-B-04 / B-3.2 | 0.5h |

### B-4. 문서 API

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-4.1 | **P1** 검색 API: `POST /documents/search`. 완료: query/top_k 경계, DocumentHit 전체 필드, count, 422/503 | FR-B-06 / B-3 | 1.0h |
| B-4.2 | **P1** 상세 API: `/documents/{document_id}`. 완료: chunk seq ASC, MANUAL enum 유지, 없는 ID 404 | FR-B-06 / B-0.2 | 1.0h |

### B-5. Tool 2종

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-5.1 | **P1** `get_equipment_context`: Service를 Tool 계약으로 감싸고 `TOOL_DB_TIMEOUT_SEC=5` 적용. 완료: 정상/NOT_FOUND/TIMEOUT/DEPENDENCY_ERROR | FR-B-03 / B-1 | 1.0h |
| B-5.2 | **P1** `search_documents`: 0건 성공 허용. 완료: 정상/TIMEOUT/MODEL_NOT_READY/DEPENDENCY_ERROR | FR-B-04 / B-3 | 1.0h |
| B-5.3 | **P1** C wrapper 의존성: B는 예외 없는 Tool JSON만 제공. 완료: C가 agent_tool_call과 latency를 기록 | FR-B-03, FR-B-04 / C-0.4 | 0.5h |

### B-6. Knowledge Frontend

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-6.1 | **P1** 관계/문서 화면 실연동: chamber/equipment 검색, node 선택, 관계, hit/score/content 펼침. 완료: 직접 URL 실데이터 복원 | FR-B-06 / B-2, B-4 | 1.5h |
| B-6.2 | **P1** 상태 검증: Loading·Error·Empty·Success. 완료: 검색 0건 Empty와 dependency Error 구분, mock 0건 | FR-B-06, NFR-17 / B-6.1 | 0.5h |

### B-7. Knowledge 평가

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| B-7.1 | **P1** 문서 골드셋: 고정 질문 3종 포함 10문항 이상. 완료: Recall@4 0.80 이상, MRR 0.70 이상 | FR-B-07 / B-3 | 1.5h |
| B-7.2 | **P1** 관계 골드셋: 챔버 4와 설비 2. 완료: 6/6, PHO downstream와 ETC boundary 포함 | FR-B-07 / B-1 | 1.0h |
| B-7.3 | **P1** 평가 artifact: 기대 문서/section/복수정답과 모델 revision·설정·fixture hash 기록. 완료: `docs/evaluation/knowledge` 결과 생성 | FR-B-07 / B-7.1~B-7.2 | 0.5h |

**B 합계 22.5h**

---

## 6. C — Agent and HITL

### C-0. Runtime 기반

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-0.1 | **P0** 001 migration 산출물: 신규 table 2, column 7, unique index 5(부분 4 + 일반 `ux_agent_tool_call_run_seq` 1), 일반 `ix_agent_run_alarm_alarm` 1. 완료: backfill 6 guard → NOT NULL → incident 부분 index 순서, 단일 transaction, `verify_migrations.py` 통과 | FR-C-01,05,08,09,14 / CM-0 | 2.0h |
| C-0.2 | **P0** 최소권한 role/grant 산출물: app/readonly/logger/n8n delivery SQL과 검증 matrix 작성. 완료: fresh/공용 적용은 CM-1.3/CM-1.5에서 수행하고 비밀 출력 0건 | NFR-01, NFR-05 / CM-0 | 1.5h |
| C-0.3 | **P0** Checkpoint 초기화 산출물: `init_checkpoint.py`, setup 자동 호출 금지. 완료: autocommit true/prepare threshold 0, table 4, thread index 3, 재실행 무변경 | FR-C-04 / CM-0 | 1.5h |
| C-0.4 | **P0** Tool wrapper 예약 행: 외부 호출 전 미완료 예약을 커밋하고 종료 후 같은 행 갱신. 완료: SUCCESS/ERROR/TIMEOUT과 crash fixture, latency 기록 | FR-C-08 / C-0.1 | 2.0h |
| C-0.5 | **P0** 영속 Tool 예산: COUNT/GROUP BY 단일 기준, 총 8, 동일 Tool 최대 4, send 최초 1회 예약. 완료: HITL/복구 후 8회 초과 0건 | FR-C-08 / C-0.4 | 1.5h |
| C-0.6 | **P0** 감사 helper: 내부 commit 없이 업무 transaction에 합류. 완료: 업무 rollback 시 감사 rollback, UPDATE/DELETE 메서드 0건 | 요구 11.1 / C-0.1 | 0.5h |

### C-1. Incident 로딩과 중복 방지

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-1.1 | **P0** incident/대표 알람: key `(lot_id, chamber_id)`, 대표 occurred at/alarm ID ASC. 완료: action trigger lot history ID는 대표 알람 값, 전체 alarm IDs는 agent_run_alarm과 evidence_json 양쪽 저장 | FR-C-01 / A-2 | 1.0h |
| C-1.2 | **P0** IncidentAlarmEvidence: distinct OOS/OOC wafer, R03, sibling counts. 완료: 계약 필드명과 결정론 정렬 | FR-C-01 / C-1.1 | 1.0h |
| C-1.3 | **P0** run 생성 3중 방어: advisory lock → 재조회 → partial index. 완료: 독립 UUID thread_id를 agent_run에, 모든 run-alarm link를 같은 transaction에 저장하고 202에 thread_id 포함, 동시 요청 활성 run 1건, model 선기록 | FR-C-09, FR-C-14 / C-0.1 | 2.0h |
| C-1.4 | **P0** 자동/수동 상태표: 자동은 이력 있으면 skip, 수동은 RUNNING/WAITING_APPROVAL은 `INCIDENT_ALREADY_RUNNING`, COMPLETED는 `INCIDENT_ALREADY_PROCESSED` 409, FAILED만 재실행. 완료: 전 상태 fixture | FR-C-09, FR-C-14 / C-1.3 | 1.0h |

### C-2. LangGraph와 자율성 Level

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-2.1 | **P1** StateGraph/State: load → tools → classify → decide → persist → gate/send → finalize. 완료: typed evidence와 checkpoint 직렬화, 비밀 저장 0건 | FR-C-01 / C-1 | 1.5h |
| C-2.2 | **P1** A/B Tool nodes: 실패 분기와 retry budget. 완료: A 1종/B 2종 mock 관통, 상한 소진 fail_run | FR-C-01, FR-C-08 / A-4, B-5 | 1.5h |
| C-2.3 | **P0** R03 추가 근거: 대표 요약 외 가장 이른 R03 알람의 lot history ID를 조건부 1회 조회. 완료: 대표 trigger 불변, r03_fdc_evidence와 전문 동시 저장 | FR-C-01, FR-C-08 / C-2.2 | 1.0h |
| C-2.4 | **P0** Level 1/2 router: Level 1 고정 순서, Level 2 결정론적 근거 충분성 분기. 완료: 승인 게이트/decide_action 공통, Level 3 미연결 | FR-C-02 / C-2.2 | 1.5h |
| C-2.5 | **P0** fail_run/heartbeat: run 생성, 모든 Node 진입/종료, Tool 시도 전/후, 승인 재개에서 last_active_at 갱신. 완료: 실패도 ended/model/latency와 `AGENT_RUN_FAILED` 기록 | FR-C-07, FR-C-09 / C-0.6 | 1.0h |

### C-3. Fault 분류와 조치 결정

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-3.1 | **P1** 분류 prompt/구조화 출력: FOC/RFM/MFD/TMD와 evidence IDs. 완료: fault_code 정답 누수 0건 | FR-C-15 / C-2 | 1.5h |
| C-3.2 | **P0** 출력 교정: schema 최초 실패 후 1회 교정. 완료: 두 번째 실패는 `CLASSIFICATION_OUTPUT` stage, 임의 기본 Fault 0건 | FR-C-07, FR-C-15 / C-3.1 | 1.0h |
| C-3.3 | **P1** 분류/결정 감사: decide 이후 `CLASSIFICATION_COMPLETED`. 완료: confidence, action/skip reason을 같은 업무 transaction에 기록 | 요구 11.1 / C-3.5~C-3.6 | 0.5h |
| C-3.4 | **P0** 구조화 증거 Repository: CD_AEI, 반복 LOT, 다음 WAFER 정상 복귀, 하류 전입. 완료: parameterized query와 양성/음성 DTO fixture | FR-C-03, FR-C-10 / C-1 | 2.0h |
| C-3.5 | **P0** 순수 decide_action: R03/고유 wafer/상향/하향/ceiling 적용. 완료: DB/LLM 접근 0회와 경계 조합 단위 테스트 | FR-C-03 / C-3.4 | 2.0h |
| C-3.6 | **P0** 연쇄 이상 판단: 순수는 조치 없음, 복합 ALM-0031은 MONITOR와 upstream 근거. 완료: 순수 단위 fixture에서 action/send 0건, 배치 순서 독립 | FR-C-10 / C-3.5 | 2.0h |
| C-3.7 | **P0** 안전 설정: HITL severity는 HIGH만 허용. 완료: 다른 값 기동 실패, anomaly 0.80 단독 HIGH/EQP_HOLD/interrupt 0건 | NFR-04 / 공통 config | 0.5h |

### C-4. 조치 저장과 승인 게이트

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-4.1 | **P0** persist_action: incident lock과 유효 action 재조회, action/approval 원자 생성, FAILED 재실행 action 재사용. 완료: created_by provenance 불변과 중간 실패 rollback | FR-C-04, FR-C-14 / C-3 | 2.0h |
| C-4.2 | **P0** approval gate/checkpoint: C-1.3의 저장된 thread_id를 LangGraph configurable key로 사용. 완료: EQP_HOLD action/approval/run WAITING_APPROVAL/APPROVAL_REQUESTED 커밋 뒤 interrupt, 같은 thread 조회/재개, 신규 run/action/approval 0건 | FR-C-04 / C-0.3, C-4.1 | 1.0h |
| C-4.3 | **P1** 자동 조치 경로: AUTO/WAITING과 system 승인자 설정 후 send. 완료: approval_request 0건 | FR-C-03 / C-4.1 | 0.5h |
| C-4.4 | **P0** 승인 결정 transaction: FOR UPDATE, 409/EXPIRED/legacy, 승인자 동기화, 반려 null 유지. 완료: 커밋 후 구조화 update_state와 같은 thread 재개 | FR-C-05 / C-4.2 | 2.0h |

### C-5. send_action과 n8n

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-5.1 | **P0** n8n workflow 산출물: MES/EMAIL mock, webhook secret, 최소권한 credential, README. 완료: import 후 secret/auth/smoke 통과 | FR-C-12 / CM-1 | 1.5h |
| C-5.2 | **P0** canonical hash/멱등 저장: action 저장값 재조회, 7개 효과 필드 NFC JSON SHA-256, run ID hash 제외. 완료: n8n 독립 재계산, 동일 ID/다른 hash 409 | FR-C-06 / C-4.1 | 2.0h |
| C-5.3 | **P0** 전송 상태/재시도/감사: WAITING/FAILED → SENDING → SENT/FAILED. 완료: 실패마다 `ACTION_SEND_FAILED`, 예산 잔여 시 run RUNNING, 소진 후만 FAILED, 성공 `ACTION_SENT` | FR-C-06, FR-C-08 / C-0.5, C-5.2 | 1.5h |
| C-5.4 | **P1** finalize 기록: 사람 승인 대기 제외 활성 latency와 token 합산. 완료: 성공/실패 모두 ended/model/latency 필수값 | FR-C-07 / C-5.3 | 0.5h |

### C-6. Agent REST API

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-6.1 | **P0** POST runs 비동기: run과 alarm links 커밋 후 background 등록, 즉시 202. 완료: HTTP에서 LLM/Tool 동기 수행 0회 | FR-C-01 / C-2 | 1.5h |
| C-6.2 | **P1** run 목록/상세: 필터, 페이지, started/id DESC, typed evidence/tool calls. 완료: 진행 중 latency와 R03 ref 계약 | FR-C-07, FR-C-13 / C-6.1 | 2.0h |
| C-6.3 | **P1** approval/action API: 목록, 결정, action 목록/상세. 완료: requested/id와 created/id 보조 정렬, delivery 포함 | FR-C-05, FR-C-13 / C-4, C-5 | 2.0h |
| C-6.4 | **P1** API contract tests: extra forbid, 404/409/422, legacy 직렬화/결정 거부. 완료: C DTO 전체 contract test | FR-C-05, FR-C-13 / C-6.1~C-6.3 | 1.0h |

### C-7. 복구 스크립트

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-7.1 | **P0** 응답 유실: timeout 후 delivery 조회부터 동일 action 재시도. 완료: downstream 효과 1회 | FR-C-06 / C-5 | 1.0h |
| C-7.2 | **P0** stale SENDING 60초: delivery 동일 hash면 action SENT와 run finalize, 없거나 hash 충돌이면 action/run FAILED. 완료: 새 action 0건, delivered_at 동기화, 효과 중복 0건 | FR-C-06, FR-C-09 / C-5.2 | 1.0h |
| C-7.3 | **P0** stale RUNNING 900초: WAITING_APPROVAL 제외, checkpoint면 같은 thread 재개, SENDING은 C-7.2 위임. checkpoint 없음은 SENT/CANCELED→COMPLETED, AUTO/APPROVED/WAITING→잔여 budget으로 같은 action send, FAILED/무action→run FAILED. 완료: 신규 객체 0건 | FR-C-09 / C-2.5, C-5.4, C-7.2, C-7.5 | 1.5h |
| C-7.4 | **P0** resume approved: checkpoint면 구조화 state 주입/같은 thread, 없으면 REJECTED/CANCELED→미전송 COMPLETED, APPROVED/WAITING→잔여 budget으로 같은 action send, 잔여 0→미호출 FAILED. 완료: terminal skip, 신규 객체 0건 | FR-C-04, FR-C-08 / C-4.4, C-5.4, C-7.5 | 1.5h |
| C-7.5 | **P0** 복구 예산/idempotency: 영속 Tool count 복원. 완료: 잔여 0이면 외부 미호출 FAILED, terminal skip | FR-C-08 / C-0.5 | 1.0h |

### C-8. 배치와 E2E

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-8.1 | **P0** 2단계 batch command: 전체 incident plan과 upstream link 계산 후 실행 State 제공. 완료: `run_pending_incidents.py --once`, 공개 batch API/button/scheduler 0건 | FR-C-09, FR-C-10 / C-1.4, C-2, C-3.5~C-3.6 | 2.0h |
| C-8.2 | **P0** E2E reset 구현/guard test: host/DB allowlist와 reset token. 완료: runtime/action/log/checkpoint 실행 데이터 0건, 입력과 checkpoint migration 보존, 공용 host 거부; CM-3.1에서 통합 | 요구 13장 / CM-0, C-0.3 | 1.5h |
| C-8.3 | **P0** 51건 전체 batch fixture: expected_actions의 incident/action/대표 trigger 10/10 일치, LOT-260005 5 alarms→LOT_HOLD 1, LOT-260004 6 alarms→EQP_HOLD 1, 3 pending approval. 완료: 두 번째 batch 신규 run/action/delivery 각 0건 | FR-C-09, FR-C-14 / C-8.1~C-8.2, CM-3.1 | 1.5h |
| C-8.4 | **P1** fresh reset별 골든 1-3: ALM-0001 AUTO/WAITING→SENT/COMPLETED, ALM-0022 같은 행 APPROVED/WAITING→SENT, ALM-0048 같은 행 REJECTED/CANCELED·승인자 null·n8n 0회. 완료: 효과 1회/0회 | FR-C-05, FR-C-06 / C-8.3 | 2.0h |
| C-8.5 | **P1** 장애 4-A/4-B: Tool 장애와 n8n 장애 fixture. 완료: 4-A는 action_history/`ACTION_SEND_FAILED` 0건; 4-B는 승인 tx 유지, 같은 action FAILED, Tool ERROR/TIMEOUT와 `ACTION_SEND_FAILED`, 잔여 budget 시 RUNNING/소진 시 FAILED, 응답 유실 delivery/effect 1회 | FR-C-06, FR-C-08 / CM-3.1, C-8.2 | 1.5h |
| C-8.6 | **P1** 복합 연쇄/순서 독립: ALM-0031 단독은 PHOTO incident 선행, 51건 shuffle batch 비교. 순수 연쇄는 C-3.6 단위 테스트만 사용. 완료: 처리 순서 무관 결과 | FR-C-10 / C-3.6, C-8.1, CM-3.1 | 1.0h |

### C-9. Agent 평가

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-9.1 | **P1** Level 1/2 평가: 4시나리오 × 3회, 동일 model/prompt/data/temperature 0.1. 완료: 완료율, Tool 수, 평균/중앙 latency, token, 결과 일치 기록 | FR-C-02 / C-8 | 2.0h |
| C-9.2 | **P1** Fault 51건 offline 평가: FOC 22/RFM 15/MFD 14와 TMD 합성. 완료: agent_run 증가 0, confusion/P/R/F1/Macro-F1, provider/model/prompt version/temperature/fixture SHA metadata artifact | FR-C-15 / C-3 | 2.0h |

### C-10. Agent Frontend

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| C-10.1 | **P1** 실행/승인 화면: 증상/관계/문서 근거, 승인/반려, 전송 상태. 완료: 실제 API 상태 전이와 409 메시지 | FR-C-13 / C-6 | 2.0h |
| C-10.2 | **P1** 조치 목록: PENDING 기본, 승인/전송/action/equipment/chamber/기간 필터. 완료: 실행 상세 이동과 label 정합 | FR-C-13 / C-6.3 | 1.0h |
| C-10.3 | **P1** 상태/polling: 2초 polling, terminal stop, 화면 이탈 취소, 재진입 복원, 4상태. 완료: timer leak 0건과 mock 0건 | FR-C-13, NFR-17 / C-10.1~C-10.2 | 1.0h |

**C 합계 71.0h**

---

## 7. D — Analytics

### D-0. D 계약 동결

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-0.1 | **P0** DTO/Tool 계약: query/validate/history/evaluation/audit DTO, extra forbid, Metric/Chart enum. 완료: 채택한 validate를 포함한 D API 5종과 AnalysisPlan Tool contract test | FR-D-01, FR-D-04~09 / CM-0.1 | 1.0h |

### D-1. DB pool과 권한

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-1.1 | **P0** source preflight: 운영/평가 기준 건수 읽기 전용 확인. 완료: runtime 최신 상태와 evaluation 초기 상태를 구분 | FR-D-03, FR-D-08 / CM-1 | 0.5h |
| D-1.2 | **P0** 권한 검증: readonly 16 table SELECT만, logger 고정 INSERT만 허용. 완료: CM-1.5 이후 기존 기본 비밀번호 로그인 실패, 허용/거부 SQLSTATE와 비밀 출력 0건 | NFR-01 / C-0.2, CM-1.5 | 1.0h |
| D-1.3 | **P0** 프로세스별 pool factory: 일반 API는 runtime readonly+logger 2개, evaluation one-shot은 eval readonly+logger 2개만 생성. 완료: pool별 cache/context 분리, 반대 DSN 미주입, fallback 0회, DSN 로그 0건 | FR-D-03, FR-D-08 / D-1.2 | 1.5h |

### D-2. sqlglot 12단계 검증기

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-2.1 | **P0** test-first fixture: 방어 D1-D6와 정상 SELECT/JOIN/GROUP. 완료: red fixture 고정 | FR-D-02 / D-0 | 1.5h |
| D-2.2 | **P0** 1-3단계: 빈 SQL, 정확히 1문장, 최상위 SELECT. 완료: 문자열 내부 semicolon 오탐 0건 | FR-D-02 / D-2.1 | 1.0h |
| D-2.3 | **P0** 4-6단계: 재귀 쓰기/DDL AST, 위험 함수, catalog/db/name 정규화. 완료: SELECT INTO/CTE write와 catalog 우회 차단 | FR-D-02 / D-2.2 | 1.5h |
| D-2.4 | **P0** 7-10단계: CTE/base 분리, public 외 schema, 무스키마 catalog, pool별 information_schema 컬럼. 완료: alias scope와 runtime/eval schema 차이 통과, audit_log/approval_request 자연어 질의는 `POLICY_REJECTED` 후 전용 API 안내 | FR-D-02 / D-1.3 | 2.0h |
| D-2.5 | **P0** 11-12단계: LIMIT 500 주입/축소/유지 후 정규화 SQL 전체 재파싱. 완료: 재검증 우회 0건 | FR-D-02 / D-2.4 | 1.0h |
| D-2.6 | **P1** 검증 결과: normalized SQL과 checks 조립. 완료: 내부 SQL/DSN 비노출과 contract test | FR-D-02, NFR-14 / D-2.5 | 0.5h |

### D-3. Analysis Plan Tool

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-3.1 | **P0** pool별 schema context: 16 table과 실제 컬럼/nullability. 완료: runtime/evaluation context 교차오염 0건 | FR-D-01 / D-1.3 | 1.0h |
| D-3.2 | **P1** enum/context 주입: judgement/rule/action과 metric 9/chart 4. 완료: 비허용 avg/pie/scatter 안전 실패, 임의 mapping 0건 | FR-D-01 / D-0.1 | 0.5h |
| D-3.3 | **P1** LLM 호출/구조화 파싱: main model, timeout 60s. 완료: 단일 model과 Pydantic 성공 계약 | FR-D-01 / D-3.1 | 1.5h |
| D-3.4 | **P1** Tool 실패 계약: `LLM_NOT_READY` 등 예외 미전파. 완료: 독립 Tool은 agent Tool 8회/agent_tool_call에서 제외 | FR-D-01 / 공통 Tool 계약 | 0.5h |

### D-4. 질의 파이프라인

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-4.1 | **P0** readonly 실행: statement timeout 5s와 LIMIT 500. 완료: 위험 함수는 validator에서 실행 0회, timeout 방어는 격리 query로 별도 검증 | FR-D-03 / D-2 | 1.0h |
| D-4.2 | **P0** QueryLog 4조합: 성공 (T,F,null,null,row), 정책 거부 (F,T,reason,null,null), 파싱/컬럼 실패 (F,F,null,error,null), DB 오류 (T,F,null,error,null). 완료: QueryLog INSERT 실패 시 생성 SQL의 app/logger pool 실행 0회, 민감정보 없는 서버 로그만 기록 | FR-D-05 / D-1.3 | 1.0h |
| D-4.3 | **P0** 오케스트레이션: plan → validate → execute → response/log. 완료: 정책 위반 재생성/실행 0회 | FR-D-03, FR-D-05 / D-2, D-3 | 1.5h |
| D-4.4 | **P0** 제한적 교정 재생성: 구문/없는 컬럼/읽기 schema 오류만 1회. 완료: 두 번째도 전체 12단계 재검증, Agent retry와 무관 | FR-D-02 / D-4.3 | 1.0h |
| D-4.5 | **P1** metric 계산: scalar/group list, ratio alias, percentile 0-100. 완료: 수치/그룹 contract test | FR-D-04 / D-4.3 | 1.0h |
| D-4.6 | **P0** chart compatibility: bar 위반→table, line 위반→bar, histogram 위반→table, table 기본 fallback. 완료: backend 확정 plan 그대로 사용, 프론트 재판정 0건 | FR-D-04 / D-4.5 | 1.0h |
| D-4.7 | **P0** POST query API: 정책 거부 200, malformed 422, `LLM_NOT_READY`/`DEPENDENCY_NOT_READY` 503. 완료: 분석 pipeline이 시작된 성공·거부·검증 실패·실행 오류에 nl_query_log_id/latency; malformed 422는 로그 보장 대상 아님 | FR-D-06 / D-4.2~D-4.6 | 1.0h |

### D-5. 이력, 평가, 감사 API

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-5.1 | **P1** 질의 이력: is_valid/is_rejected/date_from/date_to/page/size 필터와 asked_at/log_id DESC. 완료: 거부/오류 숨김 0건과 질문 재실행 | FR-D-05, FR-D-06 / D-4 | 1.0h |
| D-5.2 | **P1** 평가 이력 API: latest/page. 완료: 파일 없음 200 empty, executed/run ID DESC | FR-D-08 / D-6.4 | 0.5h |
| D-5.3 | **P0** 감사 Repository: 전용 app read connection으로 event/actor/entity type, entity ID, date from/to, page/size 조회. 완료: 동일 필터 전체 event counts, 필터와 무관한 고정 9종 선택지, before/after dict, audit UPDATE/DELETE 0건, Text2SQL 조회 0회 | FR-D-07 / A-2.5, C 감사 | 1.5h |
| D-5.4 | **P1** 감사 API/통합 test: occurred_at/audit_id DESC pagination과 entity 생애 추적. 완료: 9 이벤트 노출, GET audit-logs만 조회, DB 권한 거부 확인 | FR-D-07 / D-5.3 | 1.0h |

### D-6. Text2SQL 평가

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-6.1 | **P0** Gold Q01-Q12 fixture: 요구사항 9.1 기대값 그대로. 완료: 별도 smoke 수치 혼입 0건, ID와 값 동시 기록 | FR-D-08 / D-1.1 | 1.5h |
| D-6.2 | **P0** Defense/comparator: D1-D6, 정확 비교/실수 0.001/조건부 순서/chart x/y. 완료: attempt count와 최초/교정 SQL 기록 | FR-D-08 / D-2, D-6.1 | 1.0h |
| D-6.3 | **P0** evaluation preflight: eval readonly/log URL의 host·port·DB 동일, DB명 kosa_text2sql, manifest hash/action history 10 검증. 완료: 선택적 runtime target check는 kosa_agent이며 eval과 다름, 운영 fallback 0회, 불일치 시 시작 전 중단 | FR-D-08 / CM-1 | 1.0h |
| D-6.4 | **P1** 평가 runner/artifact: gold 12와 defense 6, `docs/evaluation/analytics/<run_id>.json` temp→rename 원자 저장. 완료: 10/12 이상, 6/6, provider/model/temperature/prompt/fixture hash와 비밀 없는 DB alias 기록 | FR-D-08 / D-6.2~D-6.3 | 1.5h |
| D-6.5 | **P1** evaluation API 검증: latest true/false와 페이지. 완료: 파일 없음/다중 run contract test | FR-D-08 / D-5.2, D-6.4 | 1.0h |

### D-7. Analytics Frontend

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-7.1 | **P1** Analytics 실연동: 질문, SQL, table, metric, chart. 완료: backend chart plan 그대로 렌더링 | FR-D-06 / D-4.7 | 1.5h |
| D-7.2 | **P1** history/evaluation: 이력 필터, 재실행, 평가 결과. 완료: 실DB/API와 empty file 처리 | FR-D-06, FR-D-08 / D-5 | 1.0h |
| D-7.3 | **P1** Audit 화면: 기간/event/actor/entity 필터, 전체 counts, before/after 펼침. 완료: 이벤트 9종 표시 | FR-D-07 / D-5.4 | 1.0h |
| D-7.4 | **P1** 상태/오류/timeout: 4상태, 정책 거부/실행 오류 구분, query timeout 150000ms. 완료: mock 0건과 정상/거부/오류 E2E | FR-D-06, NFR-17 / D-7.1~D-7.3 | 1.0h |

### D-8. 권장 기능

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| D-8.1 | **P1** 채택 권장 POST validate API: 검증만 하고 실행하지 않음. 완료: invalid 200, malformed 422, DB 실행 0회; 22 endpoint 계약에 포함 | FR-D-09 / D-2 | 0.5h |

**D 합계 37.5h**

---

## 8. Common — 문서, 배포, 통합

### CM-0. 문서와 계약 정합화

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| CM-0.1 | **P0** 문서 충돌 해결: PR 제목, anomaly field, R03 state, 검색 정렬, health 범위. 완료: FR-I-05와 readiness/개발가이드 충돌을 원본 우선으로 결정하고, health API 유지 시 요구사항 v1.9 선개정 후 원본/요약/가이드/workflow 동기화 | 프로젝트 규칙 / 전원 | 1.5h |
| CM-0.2 | **P0** API 명세 재생성: specs → DTO → contract test → CSV/Markdown/PDF 순서. 완료: CM-0.1과 A/B/C/D 확정 contract 후 health 제외 채택 22 endpoint와 Tool 5종이 세 형식에서 동일; health 유지 시 개수 재산정 | FR-I-01, FR-I-07 / CM-0.1, 각 계약 산출물 | 1.5h |
| CM-0.3 | **P1** 완료 증빙 규칙: Task별 test command/result, 요구사항 ID, artifact/PR, 미검증 항목. 완료: grep/file existence만으로 DONE 처리 0건 | 요구 13장 / 전원 | 0.5h |
| CM-0.4 | **P0** source preflight 산출물: mentor 원본에서 16 base table canonical hash의 source manifest와 `verify_source_data.py` 구현. 완료: 두 DB/public read-only profile, 비밀 없는 diff, 공용 DB write 0회 | FR-I-04 / mentor package | 1.5h |
| CM-0.5 | **P0** embedding 사전 준비: `prefetch_embedding_model.py`로 고정 revision cache와 model manifest 생성. 완료: SHA/revision 검증, CM-1.2/B-3.1 read-only 사용, runtime download 0회 | FR-B-02, FR-B-05 / CM-0.1 | 1.0h |

### CM-1. Fresh Compose bootstrap

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| CM-1.1 | **P0** Compose 기반: PostgreSQL 2 DB, Neo4j, n8n, named volumes/network, bootstrap profile. 완료: 일반 up에서 data bootstrap 0회 | FR-I-04 / CM-0 | 2.0h |
| CM-1.2 | **P0** source bootstrap: 두 PostgreSQL DB에 원본 01→02→03, graph 24/26, kosa_agent 문서 3/39와 고정 revision 1024 embedding/NULL 0. 완료: 16 table canonical hash를 두 DB와 비교, evaluation action_history 10, success marker, populated volume 기본 거부 | FR-I-04, FR-B-01, FR-B-02 / CM-0.4~CM-0.5 | 2.0h |
| CM-1.3 | **P0** runtime 통합 적용: kosa_agent에만 C migration/checkpoint와 role 4종 grant, evaluation 초기 상태 유지. 완료: autocommit true/prepare threshold 0, checkpoint table 4/index 3, 앱 시작 setup 0회, 허용/거부 matrix와 비밀 출력 0건 | FR-C-04, NFR-01, NFR-05 / CM-1.2, C-0.1~C-0.3, D-0.1 | 1.5h |
| CM-1.4 | **P0** 재기동/reset 안전성: 재기동 hash 불변, force는 backup/team/reset token/host-DB allowlist. 완료: 공용 서버 force 거부와 n8n 보존 | 요구 13장 / CM-1.1~CM-1.3 | 1.5h |
| CM-1.5 | **P0** 공용 교육장 서버 credential 전환: 팀/멘토 공유와 preflight 후 role 4종 적용, 기존 project-role PID만 종료, 전 DB 검증 뒤 LOGIN 활성화. 완료: 기존 기본 비밀번호 로그인 실패, readonly 16 SELECT와 app/logger/n8n 허용·거부 matrix, runtime 3 pool 재시작, 비밀/DSN 출력 0건 | NFR-01, 요구 13장 / CM-0.4, C-0.2, D-0.1 | 2.0h |

### CM-2. 애플리케이션과 배포 통합

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| CM-2.1 | **P1** 단일 FastAPI: A/B/C/D Router, error handler, OpenAPI. 완료: CM-0.2의 채택 endpoint 22개와 host 개발 `/docs`, `/openapi.json` | FR-I-01, FR-I-07 / CM-0.2, 각 API | 1.5h |
| CM-2.2 | **P1** 단일 React 8화면: `/`→dashboard, 8개 업무 route 직접 접근/deep link, alarm/agent/action 이동, dashboard↔analytics와 audit-logs navigation. 완료: 모든 도메인 화면 실 API 복원 | FR-I-02, FR-I-03 / A-7, B-6, C-10, D-7 | 1.5h |
| CM-2.3 | **P1** production image/Nginx: `/api` reverse proxy, proxy read timeout 150s, Frontend API base `/api` 단일 사용. 완료: 배포 API root `/api`와 `/api/docs`·`/api/openapi.json`; host 개발 root 빈 값; 브라우저 localhost/Docker 서비스명 호출 0건 | FR-I-04 / CM-2.1~CM-2.2 | 2.0h |
| CM-2.4 | **P0** 환경 allowlist/보안: root env_file 전체 전달 금지, 변수 보간+서비스별 allowlist. Backend는 runtime DB URL 3개, evaluation은 eval URL 2개만 주입. 완료: 반대 URL/불필요 credential 0건, blank/change_me/default secret 준비 실패, 값의 로그/readiness 노출 0건 | NFR-01, NFR-02, NFR-12, NFR-14 / CM-1 | 1.5h |
| CM-2.5 | **P0** 재현성 산출물: image digest, A artifact와 CM-0.5 embedding cache read-only mount, n8n import, `verify_n8n_ready.py`, 모델 manifest 검증. 완료: runtime download/train 0회, 일반 Backend `./docs/evaluation:/app/docs/evaluation:ro`, evaluation profile만 같은 mount rw | NFR-15 / A-3.4, B-3.1, C-5.1, D-6.4 | 1.5h |

### CM-3. 격리 테스트와 최종 검증

| ID | 작업 및 완료 기준 | 근거 / 선행 | 시간 |
|---|---|---|---|
| CM-3.1 | **P0** test Compose: 전용 DB/volume, 정상 1-3은 실제 export/import n8n workflow, 4-B만 stub/test-webhook profile, 공용 host 차단, C-8.2 reset 연동. 완료: 기본 Compose service/volume 재사용 0건 | NFR-18 / CM-1, C-8.2 | 2.0h |
| CM-3.2 | **P1** 검증 matrix: Ruff/pytest, Tool contract, PostgreSQL/Neo4j/pgvector/checkpoint integration, npm lint/build. 완료: C-8/C-9와 CM-2 결과를 포함해 PR에 실제 명령/결과 기록 | 요구 13장 / C-8, C-9, CM-2 | 1.0h |
| CM-3.3 | **P1** 공동 최종 골든 rerun: 1 자동, 2 승인, 3 반려, 4-A Tool override, 4-B n8n 장애. 완료: 정상 1-3은 실제 API/DB/n8n/React, 장애만 override, 중복 효과 0건과 UI 증빙 | 요구 부록 B / CM-3.2, C-8.3~C-8.6 | 2.0h |
| CM-3.4 | **P1** 최종 실행 가이드/결과서: fresh 설치, 일반 재기동, 복구, 평가, 미검증 사항. 완료: 다른 PC에서 재현하고 4명 공동 승인 | 역할분담 15.1 / CM-3.2~CM-3.3 | 1.5h |

**Common 합계 29.5h**

---

## 9. 도전 과제 — 채택 범위 공수 제외

| ID | 담당 | 작업 및 완료 기준 | 시간 |
|---|---|---|---|
| A-X1 | A | **P2** 모델 고도화. 필수 rule/IF 결과와 분리하고 같은 평가 protocol로 비교 | 2.0h |
| B-X1 | B | **P2** pg_trgm hybrid 검색. 기존 vector API 계약을 바꾸지 않고 비교 평가 | 2.0h |
| C-X1 | C | **P2** Level 3 ReAct. 필수 Level 1/2와 같은 State/Tool을 사용하고 초기 graph에 기본 연결하지 않음 | 3.0h |
| D-X1 | D | **P2** `generate_analysis_plan` MCP wrapping. LangGraph 연결 없이 독립 Analytics Tool로 유지 | 1.5h |
| **합계** | | 채택 범위 완료 후 별도 결정 | **8.5h** |

---

## 10. 선행 관계와 실행 순서

| Gate | 완료 조건 | 후속 작업 |
|---|---|---|
| G0 문서/환경 계약 | CM-0 완료, source verifier/manifest/model cache와 비밀 없는 env key 확정 | CM-1, A-0, B-0 |
| G1 runtime 기반 | C-0 산출물→CM-1 적용, A/B 공용 기반 검증, D process별 pool 검증 완료 | 각 Service/Tool 구현 |
| G2 핵심 판단 경로 | A rule/model, B relation/search, C incident/graph/decide, D plan/validator 완료 | API, query pipeline, batch/recovery |
| G3 API/화면 | Tool 계약과 Router/DTO contract 통과 | 실 API frontend 연동 |
| G4 복구/E2E 기반 | C 복구와 reset, CM-3.1 격리 환경, C-8.1 test harness 완료 | 격리 E2E와 Level/Fault 평가 실행 |
| G5 배포 승인 | C-8/C-9, CM-2/CM-3, 골든 1/2/3/4-A/4-B 통과 | 최종 시연/제출 |

## 11. 완료 보고 체크리스트

- 실행한 테스트 명령과 결과를 기록했는가.
- 요구사항 ID별 충족 여부를 기록했는가.
- 공용 DB가 아닌 격리 DB에서 E2E를 실행했는가.
- PostgreSQL, Neo4j, n8n, React 실제 연동 결과가 있는가.
- 공용 서버의 기존 기본 비밀번호 로그인이 실패하고 최소권한 허용/거부 matrix가 통과했는가.
- `.env`, 비밀번호, API Key, 전체 DSN, model cache가 커밋에 없는가.
- 일반 Backend의 평가 artifact mount는 read-only이고 evaluation one-shot만 write 가능한가.
- API/Tool 계약 변경 시 원본 사양과 API 명세 3종을 함께 갱신했는가.
- 미완료와 미검증 항목을 숨기지 않았는가.

## 12. 근거 문서

| 번호 | 문서 | 번호 | 문서 |
|---|---|---|---|
| 1 | `docs/specifications/요구사항정의서_v1_9_최종.md` | 8 | `docs/ai-context/05-agent-workflow.md` |
| 2 | `docs/specifications/시스템설계서_v1_10_최종.md` | 9 | `docs/ai-context/06-frontend-guide.md` |
| 3 | `docs/specifications/FDC_프로젝트_역할분담_v9.6(최종).md` | 10 | `docs/ai-context/07-testing-guide.md` |
| 4 | `docs/ai-context/01-project-rules.md` | 11 | `docs/ai-context/tasks/A-detection.md` |
| 5 | `docs/ai-context/02-domain-rules.md` | 12 | `docs/ai-context/tasks/B-knowledge.md` |
| 6 | `docs/ai-context/03-database-rules.md` | 13 | `docs/ai-context/tasks/C-agent.md` |
| 7 | `docs/ai-context/04-api-tool-contracts.md` | 14 | `docs/ai-context/tasks/D-analytics.md` |
