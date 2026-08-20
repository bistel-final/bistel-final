# FDC 프로젝트 역할분담

**LangGraph 기반 반도체 FDC 이상감지 에이전트 및 데이터 플랫폼**

---

## 1. 문서 정보

| 항목 | 내용 |
|---|---|
| 버전 | v10.1 작업본 |
| 작성일 | 2026.08.19 |
| 기준 자료 | 멘토님 제공 최종 `project.zip` (`SHA-256: e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3`) |
| dataset epoch | `fdc_final_20260818` |
| 기준 데이터 | `sample/data` 9개 CSV, `sample/schema/03_schema_clean.sql`, `sample/ontology/master.cypher`, `sample/rag`, `mvp/gen_sample_data.py` |
| 선행 문서 | 요구사항정의서·시스템설계서 최종 데이터 전환 작업본 |
| 후속 산출물 | API 명세 v3 리뷰·확정, WBS v5 작업본, 역할별 Task |

### 1.1 개정 목적

최종 패키지의 실제 CSV와 재현 가능한 Generator를 기준으로 데이터·기능·역할을 다시 정렬한다.
기존 `kosa_0813.zip`의 126/47건 알람, 10/48건 조치, 38/81 그래프 및 공개 Fault
정답 없음 전제는 본 역할분담의 구현 근거로 사용하지 않는다.

본 문서는 업무 소유권을 정한다. Task ID, 일정, 선행관계 및 완료 상태는 본 문서를 확정한 뒤
새로 작성할 WBS v5에서 관리한다. 기존 WBS v4의 완료 표시는 이력으로 보존하되 새 기준의 완료로
자동 승계하지 않는다.

---

## 2. 최종 기준선

### 2.1 데이터 기준

| 항목 | 최종 기준 |
|---|---:|
| `dim_parameter` | 8 |
| `lot_history` | 600 |
| `fdc_trace` | 14,400 |
| `summary_data` | 4,800 |
| `evaluation` | 4,800 (`IN 4,538 / OOC 216 / OOS 46`) |
| 저장 알람 | 189 (`TRACE 138 / SUMMARY 51`) |
| 파생 R03 | 3 |
| Agent incident | 12 |
| reference action | 12 (`MONITORING 5 / WARNING 4 / EQP_HOLD 3`) |
| `metrology` | 48 |
| Neo4j | 44 nodes / 85 relationships |

저장 알람과 R03는 구분한다. 저장 알람 기본 조회는 189건이며 R03를 포함한 사건 표시는 192건이다.
`action_history` 12건은 결과 비교용 reference fixture이고 Agent Runtime 초기 seed가 아니다.

### 2.2 공개 합성 라벨 경계

최종 `lot_history.fault_code`에는 Generator가 만든 공개 합성 라벨이 있다.

| 값 | 건수 |
|---|---:|
| NRM | 554 |
| FOC | 15 |
| RFM | 12 |
| MFD | 13 |
| TMD | 2 |
| OTH | 4 |

- `public_fault_ground_truth_available=true`, `label_source=SYNTHETIC_GENERATOR`,
  `production_ground_truth_available=false`, `usage_scope=EVALUATION_ONLY`로 기록한다.
- 합성 라벨은 격리된 평가기에서만 읽는다.
- 모델 feature·학습 입력·Agent State·Tool 반환·RAG·프롬프트·조치 규칙·Text2SQL 사용자 조회에는
  합성 라벨을 주입하지 않는다.
- Agent 출력은 `predicted_fault_code`, 평가 정답은 `ground_truth_fault_code`로 명명한다.
- 합성 데이터 평가 결과를 생산 환경 성능으로 표현하지 않는다.

### 2.3 anomaly score 경계

anomaly score는 모델 상태와 이상 근거를 설명하기 위한 보조 신호다. 점수 또는 점수 임계값으로
`MONITORING`을 `WARNING`으로 올리거나 `EQP_HOLD`를 만들지 않는다. 조치는 다음 결정론적 규칙만
사용한다.

| 사건 | 조치 | 승인 | 전송 |
|---|---|---|---|
| SUMMARY OOC만 존재 | MONITORING | 불필요 | 없음 |
| TRACE OOS 존재 | WARNING | 불필요 | n8n SMTP 이메일 |
| R03 존재 | EQP_HOLD | 필수 | 승인요청 이메일, 승인 후 Kafka MES Mock |

규칙 우선순위는 `R03 > TRACE OOS > SUMMARY-only OOC`다. 같은 incident에 여러 조건이 있으면
가장 높은 우선순위의 조치 하나만 생성한다.

---

## 3. 공통 개발 원칙

### 3.1 Full-stack 기능 책임

각 담당자는 자신이 맡은 기능의 데이터 접근, 계산·서비스, Tool, FastAPI, React 연결,
contract/integration test 및 평가 artifact까지 책임진다. 공통 파일 변경은 담당자 한 명이 독점하지
않으며 계약 소유자와 소비자가 함께 검토한다.

### 3.2 5개 사용자 화면

| 사용자 화면 | 주 책임 | 협업 |
|---|---|---|
| 1. 알람 대시보드 | A | C의 실행 결과 집계 연동 |
| 2. 알람·Trace | A | C의 Agent 실행 진입점 |
| 3. Agent 분석 | C | A 감지, B 근거, D 감사 조회 |
| 4. 문서 검색 | B | C 근거 deep link |
| 5. Ontology | B | Neo4j 44/85 관계 시각화, C의 근거 연결 |

기존 React 상세 route나 승인·감사 탭은 위 다섯 화면의 내부 route·subview로 유지할 수 있다.
별도 사용자 화면 수를 늘리는 것은 필수 범위가 아니다.

현재 저장소 React의 7개 메뉴와 기존 route는 아직 이 목표에 맞춰지지 않았으므로 완료로 승계하지
않는다. WBS v5에서 5개 메뉴·호환 projection·Ontology 보안 API·감사 wrapper 소비를 별도
Frontend adapter Task로 배정한다.

### 3.3 최소 API와 확장 API

최종 패키지의 물리 React `App.jsx`와 `검토질문_답변.html`이 확정한 다섯 화면을 우선한다.
`02_화면별_API_가이드.md`에는 제거된 Text2SQL 화면이 남아 있으므로 API 목록 부분은 stale로
취급한다. 실제 `frontend/src/lib/api.js`가 노출하는 9개 wrapper를 필수 호환 계약으로 둔다.
`api.audit()`는 참고 페이지에서 아직 소비하지 않으므로 Agent 감사 subview 연결을 별도 완료
기준으로 둔다. Ontology 화면은
API 없이 Neo4j Browser와 기본 계정을 직접 노출하므로, 프로젝트는 이를 대체하는 read-only
`GET /relations/chambers/{chamber_id}`를 보안 필수 API로 추가한다. 따라서 public 필수는 호환 9개 + 보안 1개다.
Text2SQL과 기존 상세·페이지·재시도 API는 선택 확장으로 관리한다. 동일 path가 요청 조건에 따라
배열과 페이지 객체를 번갈아 반환하지 않는다. 상세 계약은 `API명세서_v3_작업본.md`를 따른다.

### 3.4 공용 DB와 파괴적 작업

- 최종 패키지 전체를 저장소 코드에 덮어쓰지 않는다. 데이터·DDL·ontology·RAG·Generator만
  출처와 hash를 기록해 선별 수용한다.
- PostgreSQL fresh DDL은 기존 공용 DB에 직접 실행하지 않는다. 대상 DB guard와 manifest 검증을
  통과한 bootstrap runner로 적용한다.
- 제공 `master.cypher`의 `MATCH (n) DETACH DELETE n`은 공용 Neo4j에서 직접 실행하지 않는다.
  staging graph 검증 후 안전한 교체 절차를 사용한다.
- Runtime DB는 action 0건에서 시작한다. reference action 12건은 격리 평가 fixture에서만 사용한다.

---

## 4. 최종 역할 요약

| 역할 | 담당자 | 핵심 책임 | 필수 API | 화면·평가 책임 |
|---|---|---|---|---|
| Common·통합 | 방대혁 주도, 전원 리뷰 | 최종 source intake, epoch·manifest, DB/graph 안전 적재, 공통 Enum·DTO·오류·감사 쓰기 계약 | health는 업무 API 외 | 통합 E2E·배포·복구 |
| A. Detection | 신동원 | Summary/evaluation/알람/R03 결정론 재현, 모델 score 근거, 합성 라벨 격리 평가 | `GET /alarms`, `GET /trace`, `GET /parameters` | 화면 1·2, 감지·모델 평가 |
| B. Knowledge | 강연권 | Neo4j 44/85 안전 검증, RAG 문서 정정·검색, 근거 provenance | `POST /documents/search`, 보안 필수 `GET /relations/chambers/{chamber_id}` | 화면 4·5, 관계·검색 평가 |
| C. Agent·HITL | 방대혁 | LangGraph, 원인 가설, 3단계 조치, 승인, n8n SMTP, Kafka MES Mock | 호환 4개 + 필수 내부 delivery callback | 화면 3, 상태 전이·E2E |
| D. Analytics·Audit | 천승현 | 감사 read model/API, 선택 확장 Text2SQL·통계·차트 | `GET /audit-logs` | 화면 3 감사 탭, 선택 확장 SQL 방어·정확도 |

---

## 5. Common — 최종 source intake·통합

### 5.1 담당 범위

- `project.zip`과 선별 파일의 SHA-256, 행 수, schema, PK/FK 및 재현 결과를 manifest로 고정한다.
- 최종 데이터 epoch를 새로 발급하고 이전 `kosa_0813` epoch와 혼합되지 않게 한다.
- 원본은 불변 보관하고, 필요한 정정이 있으면 원본과 corrected 산출물을 분리한다.
- PostgreSQL 3개 논리 DB와 Neo4j의 대상·권한·파괴적 작업 guard를 유지한다.
- **Runtime schema는 팀 소유다.** 최종 패키지 `03_schema_clean.sql`은 base 9개 table만 만들고
  참고 Backend는 실행 이력을 메모리로 합성한다. Common이 최종 base DDL 위에 Agent 실행·승인·
  조치·감사 table migration 계보를 설계·적용한다.
- 공통 `AlarmRef`, Action/Approval/Delivery Enum, 오류 응답, append-only audit 계약을 관리한다.
- 호환 필수 API 9개와 Ontology 보안 필수 API 1개의 OpenAPI·React contract test를 통합한다.
- `kosa_text2sql`을 Text2SQL 화면 활성 여부와 무관한 격리 evaluation/reference DB로 유지하고,
  선택 Text2SQL에는 별도 readonly projection만 제공한다.

### 5.2 감사로그 소유권

감사로그는 단일 담당자가 사후에 `action_history`에서 합성하지 않는다.

- Common: event enum, entity mapping, append-only table·helper 및 트랜잭션 규칙
- 각 도메인: 자기 업무 이벤트를 해당 업무 트랜잭션 안에서 기록
- C: Agent 실행, 가설 생성, 승인, action delivery 이벤트 기록
- D: `GET /audit-logs` read model, 필터·정렬·화면 표시

감사로그 UPDATE·DELETE API는 만들지 않는다.

### 5.3 완료 기준

- 실제 최종 CSV 기준 9개 테이블 수치와 Generator 재현 결과가 manifest에 일치한다.
- 126/47, 42/216/4542, 10/48, 38/81 등 구 기준을 구현 gate에서 사용하지 않는다.
- bootstrap은 대상 오입력·중복 실행·부분 실패를 거부하거나 원자적으로 복구한다.
- source fixture와 Runtime 데이터가 섞이지 않는다.

---

## 6. A — Detection Full-stack

### 6.1 담당 범위

- Trace를 집계해 Summary 4,800건을 결정론적으로 재현한다.
- evaluation `IN 4,538 / OOC 216 / OOS 46`, TRACE 138, SUMMARY 51을 재현한다.
- R03 3건을 저장 알람과 분리해 파생하고 incident 집계에 포함한다.
- 최종 `dim_parameter` 8행과 Trace `seq_no 0..5`를 그대로 검증한다. 이미 최종 데이터에 반영된
  값을 다시 보정하는 변환은 만들지 않는다.
- 비지도 anomaly score를 만들 수 있으나 조치 결정에는 전달하지 않는다.

### 6.2 모델·평가 책임

- 합성 공개 라벨은 평가 loader에서만 읽고 Runtime repository와 타입을 분리한다.
- 모델 score 재현성, LOT 단위 분리, feature leakage 0건을 검증한다.
- 후보 model·feature·normalization·threshold는 공개 label join 전에 고정하고, 합성 holdout
  결과를 같은 revision의 재선택·재튜닝에 사용하지 않는다.
- A는 공개 합성 라벨을 읽는 평가 loader·split·누수 방지와 anomaly detection 평가 artifact를
  제공한다. Agent의 5-class 원인 가설 평가는 C가 이 격리 artifact를 소비해 수행한다.
- metrology 평가는 48/600 lot_history 표본 coverage를 함께 기록하고 전체 데이터 성능으로
  외삽하지 않는다.
- 성능 결과에는 `label_source=SYNTHETIC_GENERATOR`와
  `production_ground_truth_available=false`를 표시한다.
- score와 규칙 감지 결과의 상관은 설명 자료일 뿐 조치 threshold가 아니다.

### 6.3 API·화면 책임

- 필수: `GET /alarms`, `GET /trace`, `GET /parameters`
- 선택 확장: dataset bounds, dashboard summary, source-aware alarm detail, paged 목록
- 화면 1·2에서 실제 API와 Loading/Error/Empty/Success 상태를 연결한다.
- 알람 식별은 `(source, alarm_id)`이며 source 없는 ID만으로 deep link를 만들지 않는다.
- 최종 참고 React의 축약 필드는 Backend 호환 projection으로 한시 지원하고 canonical field로
  교체하는 adapter Task를 수행한다.

---

## 7. B — Knowledge Full-stack

### 7.1 담당 범위

- 최종 `master.cypher`를 독립 파싱해 44 nodes / 85 relationships와 필수 속성·방향·중복을 검증한다.
- 공용 Neo4j 갱신이 필요하면 Common의 기존 destructive-safe loader를 재사용한다(신규 구현 없음).
- 최종 RAG 원문을 graph·데이터·조치 정책과 교차 검토해 정정본을 만든다. 원본은 보존하고
  정정본을 정본으로 적재한다(overlay·corpus revision 없음).
- 문서 검색 결과에 document/chunk와 실제 근거 내용을 반환한다.
- 검색은 임베딩 기반 벡터 검색으로 구현한다. 참고 구현의 키워드 스코어는 파일럿이다.
  임베딩은 배포본 ①의 `BAAI/bge-m3`·1024를 유지하고 재선정하지 않는다. 모델은 process당
  1회 생성해 재사용한다.

### 7.2 RAG 정정 원칙

- Neo4j가 공정 순서만 정의할 때 특정 설비 간 고정 상·하류 관계를 문서에서 사실로 만들지 않는다.
- anomaly score로 조치를 상향한다는 구 문구를 제거한다.
- metrology FAIL·여러 LOT 반복·하류 진행으로 조치를 상향하거나 원인 설명·후속 정상으로 하향한다는
  문구를 제거하고, 3단계 alarm rule 외 신호의 조치 개입을 금지한다.
- `ACT-0001~0010`만을 전체 조치 집합으로 간주한 구 10건 서술을 근거로 쓰지 않는다. 최종
  `ACT-0001~0012` reference는 평가·화면 fixture로만 사용한다.
- R03를 같은 chamber·parameter·recipe step의 연속 run==3으로 명시하고 서로 다른 key의 OOS를
  단순 합산하지 않는다.
- R01은 raw 한 점의 USL/LSL 이탈 즉시 TRACE 알람으로 통일하고 Fault 후보에 `OTH`를 포함한다.
- PH-9000 본문은 EQP01~03·RECIPE01/03, ET-7500 본문은 EQP04~06·RECIPE02/04 범위로 정정한다.
- EQP_HOLD EMAIL은 승인 요청 알림이고 Kafka MES Mock은 승인 후에만 실행된다고 구분한다.
- 원문, 정정 사유, corrected hash, embedding model·dimension을 provenance로 남긴다.

### 7.3 API·화면·평가 책임

- 호환 필수: `POST /documents/search`
- 보안 필수: `GET /relations/chambers/{chamber_id}`
- 선택 확장: 장비·챔버별 관계 조회, 문서 상세
- 화면 4의 문서 검색과 화면 5의 Ontology를 구현하고 Agent 화면의 근거 deep link를 연결한다.
- 최종 reference frontend의 Neo4j Browser iframe과 화면에 노출된 `neo4j/password`는 수용하지
  않는다. Browser 직접 접속 대신 Backend의 read-only graph API만 호출한다.
- 독립 graph fixture, Recall@4, MRR, 실패 사례를 평가 artifact에 기록한다.

---

## 8. C — Agent·HITL·n8n·Kafka Full-stack

### 8.1 담당 범위

- source-aware `AlarmRef`를 incident `(lot_id, chamber_id)`로 해석하고 대표 알람을 결정론적으로 고른다.
- LangGraph가 PostgreSQL·Neo4j·RAG·계측 근거를 모아 원인 가설을 생성하게 한다.
- LLM 출력 `predicted_fault_code`를 공개 합성 정답과 분리한다.
- 3단계 규칙으로 action을 결정하고 EQP_HOLD만 사람 승인을 요구한다.
- n8n SMTP 이메일과 Kafka MES Mock을 별도 delivery로 관리한다.
- n8n workflow 4종을 직접 제작해 `deploy/n8n/`에 커밋한다. 최종 패키지에는 import 가능한
  JSON이 없고 `docs/07_n8n_워크플로_제작가이드.md` §8이 제작·커밋을 지정한다.
  `WF1-alarm-to-agent` · `WF2-notify-email` · `WF3-mes-hold` · `WF4-result-writeback`
- 최종 패키지 compose에 n8n 서비스가 없으므로 팀 compose에 n8n 컨테이너 정의를 추가한다.
- Kafka MES Mock은 필수 범위다. broker 운영 위치(공용 1벌 / 팀원 로컬)만 compose 통합 시 확정한다.

### 8.2 조치·전송 책임

- MONITORING: delivery 없음
- WARNING: 승인 없이 n8n SMTP 이메일
- EQP_HOLD: 승인요청 이메일 후 WAITING, `APPROVED`일 때만 Kafka `fdc.actions` 발행,
  `REJECTED`이면 Kafka 발행 없음
- Backend worker는 승인 트랜잭션에서 Kafka를 직접 발행하지 않고 서명된 n8n webhook을 호출하며,
  n8n Kafka Producer가 `fdc.actions`에 발행한다.
- 이메일 성공과 Kafka 성공은 서로 독립된 channel delivery 상태와 멱등성 키를 갖는다.
- 외부 API의 `MES`는 내부 `MES_MOCK`으로 매핑한다. 실제 MES 연동으로 표현하지 않는다.
- anomaly score는 Agent 근거에 표시할 수 있지만 action 함수의 입력으로 사용하지 않는다.

### 8.3 API·화면·평가 책임

- 필수: `GET /agent/runs`, `POST /agent/ask`, `GET /approvals`,
  `POST /approvals/{approval_id}/decision`
- 필수 내부: `POST /internal/actions/{action_id}/delivery` n8n·Kafka worker write-back
- 승인 public body는 `APPROVED|REJECTED`; 내부 `APPROVE|REJECT` Enum은 boundary adapter에서만 사용한다.
- 선택 확장: Agent 실행·상세·재시도, action 상세, channel 재전송
- 화면 3의 실행·승인·action 상태를 연결한다.
- `action_id`·`approval_id`로 run과 승인을 연결하고 chamber-only 검색을 제거한다. `api.audit()`
  wrapper를 실제 감사 subview에서 소비한다.
- incident 12개 기준 golden flow와 중복 실행·동시 승인·재전송·복구를 검증한다.
- Fault 5-class 평가는 단일 distinct non-NRM 합성 라벨을 가진 TRACE incident 7건에만 적용한다.
  SUMMARY-only NRM incident 5건은 `NO_INJECTED_FAULT`, 서로 다른 비정상 라벨이 섞인 incident는
  `AMBIGUOUS_LABEL`로 제외·별도 보고하며 임의 OTH 정답이나 다수결 정답을 만들지 않는다.

---

## 9. D — 감사·선택 확장 Analytics Full-stack

### 9.1 담당 범위

- 공통 append-only 감사로그의 조회 repository/API와 화면을 담당한다.
- Text2SQL은 최종 5화면과 필수 인수 기준에서 제거됐다. 일정과 필수 E2E를 막지 않는 선택
  확장으로만 유지한다.
- 선택 구현 시 최종 schema와 허용 column만 조회하고 SQL AST 방어, timeout, row limit,
  deterministic 통계·차트 plan 및 질의 이력을 관리한다.

### 9.2 감사 조회 책임

- `audit_log`를 직접 조회하며 `action_history`에서 감사 사건을 사후 합성하지 않는다.
- `occurred_at DESC, audit_id DESC`로 안정 정렬한다.
- 동일 필터의 event count와 page 결과를 구분한다.
- 감사 데이터의 생성·수정 권한은 갖지 않으며 UPDATE·DELETE를 제공하지 않는다.

### 9.3 API·화면·평가 책임

- 필수: `GET /audit-logs`
- 선택 확장: 자연어 질의, SQL 검증, 질의 이력, Text2SQL 평가 이력, paged 감사 조회
- 화면 3의 감사 subview를 연결한다. Text2SQL을 구현할 때만 별도 route/subview를 활성화한다.
- 선택 Text2SQL의 정책 거부는 SQL 미실행 상태의 구조화된 정상 결과로 반환하고 요청 형식
  오류와 구분한다.

---

## 10. 역할 간 계약

| 제공자 | 소비자 | 고정 계약 |
|---|---|---|
| Common | 전원 | source epoch·manifest, 공통 Enum·DTO·오류, audit append helper, 안전 DB profile |
| A | C·D | `AlarmRef`, 규칙 알람, Summary·Trace, nullable score evidence, 격리 평가 결과 |
| B | C | graph relation ID, document/chunk ID |
| C | A·D | Agent run, approval, action, channel delivery 상태와 감사 이벤트 |
| D | 전원 | schema allowlist, Text2SQL·audit read model, 평가 snapshot |

### 10.1 충돌 해결 규칙

1. 공개 승인 요청은 `APPROVED|REJECTED`를 사용하고 내부 Enum은 adapter에서 변환한다.
2. 알람은 항상 `AlarmRef={source,alarm_id}`로 식별한다.
3. Agent 가설과 평가 정답은 각각 `predicted_fault_code`와 `ground_truth_fault_code`로 분리한다.
4. 외부 채널 `MES`는 내부 구현 `MES_MOCK`으로 매핑한다.
5. 9개 최소 API의 배열 응답과 페이지 응답을 같은 path에서 혼용하지 않는다.
6. 감사 쓰기 계약은 Common, 업무 event 쓰기는 각 도메인, 조회 API·화면은 D가 소유한다.

---

## 11. 문서·구현 전환 원칙

- 본 역할분담은 WBS가 아니다. 확정 후 WBS v5와 역할별 Task를 새로 작성한다.
- 기존 구현은 `유지`, `재검증`, `대체`, `폐기`로 분류하고 자동 완료 처리하지 않는다.
- 공통 안전장치, 트랜잭션, 최소권한, 멱등성, append-only audit는 유지 후보이다.
- 구 데이터 수치, no-GT 전제, score 조치 상향, 38/81 graph gate는 대체 또는 폐기 후보이다.
- 기존 `IncidentModelSignal.action_threshold`와 score 기반 action gate는 조치 입력에서 제거한다.
  score DTO가 필요하면 표시·근거 provenance만 남기고 `decide_action`과 분리한다.
- 문서와 contract test가 확정되기 전 공용 DB를 최종 데이터로 덮어쓰지 않는다.
