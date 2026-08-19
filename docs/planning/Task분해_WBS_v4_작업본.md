# 팀 전체 Task 분해 WBS v4 작업본

> [!CAUTION]
> 이 문서는 `kosa_0813` 기준의 이전 epoch 이력이다. Task 상태·선행관계를 신규 작업에 승계하지
> 않으며, v2.1 상위 문서 확정 후 WBS v5를 새로 작성한다.

> 기준 기획: `신규데이터_정답라벨제거_전환기획_v1.md`
> 기준 역할: `FDC_프로젝트_역할분담_v10_0_작업본.md`
> 데이터 epoch: `kosa_0813.zip`
> 문서 상태: 공개 Fault 정답이 없는 신규 데이터 전환 작업본
> 작성 단위: 독립 구현·검증이 가능한 1.0~2.0시간
> 채택 범위 예상 공수: **212.0h**

## 1. 적용 원칙

- Task 범위·선행관계·완료 기준이 바뀔 때 WBS 버전을 갱신한다. 담당자·상태·일정은 Notion에서 관리한다.
- 기존 데이터의 고정 Fault 정답, 고정 알람·조치 ID, supervised 분류 성능 수용 기준을 사용하지 않는다.
- `ground_truth_available=false`인 평가에서 분류 성능 지표를 계산하지 않는다. corrected generator가
  만든 별도 평가 artifact에서만 `label_source=SYNTHETIC_GENERATOR`를 사용하며 실제 Fault GT로
  해석하지 않는다.
- 신규 ZIP·원본 Generator는 수정하지 않고 corrected generator·corrected copy를 생성하며 manifest로
  보정 전후를 검증한다.
- 합성 라벨은 `usage_scope=EVALUATION_ONLY`로 격리하고 학습 feature·Runtime·Agent·Text2SQL·RAG
  입력에 사용하지 않는다.
- `[팀 잠정]` anomaly gate는 검증된 `action_threshold`에서 Summary-only `MONITORING`을 `WARNING`으로만
  상향한다. 기본 조치를 하향하거나 R03 없이 `EQP_HOLD`를 만들 수 없고, threshold 미검증 또는 score
  NULL이면 기본 규칙을 유지한다.
- 개인 로컬 DB를 두지 않고 학원 공용 PostgreSQL 서버의 논리 DB를 사용한다.
- P0는 데이터·계약·보안·중복 방지 선행 작업, P1은 필수 기능, P2는 후속 확장이다.
- 모든 완료 보고에는 실행 명령, 결과, 관련 FR, artifact 경로, 미검증 사항을 포함한다.
- v4 Task ID는 모두 `V4-` namespace를 사용한다. 기존 v3의 bare ID 상태를 v4 Task에 자동 승계하지 않는다.

### 1.1 v3 이력 보존과 v4 전환

- v3 WBS와 Notion의 기존 Task는 완료·진행 이력으로 동결한다. 삭제하거나 v4 의미로 덮어쓰지 않는다.
- Notion에는 `V4-*` ID로 신규 Task를 생성한다. 같은 숫자나 비슷한 제목이어도 기존 상태를 복사하지 않는다.
- 기존 구현을 재사용할 수 있으면 신규 데이터·계약·DB 구조로 다시 검토하고, 통과한 코드·테스트만 해당 v4 Task의 증빙으로 연결한다.

| v3 이력 ID | 기존 범위 | v4 처리 |
|---|---|---|
| `CM-0.1` | 구 문서 충돌 해소 | 이력 동결. 신규 data epoch와 계약은 `V4-CM-0.1~0.3`에서 다시 확정 |
| `CM-0.2` | 구 API 명세 재생성 | 이력 동결. 신규 최소 API·Tool 계약 기준으로 `V4-CM-0.3`과 역할별 API Task에서 재검토 |
| `CM-0.3` | 완료 증빙 규칙 | 지침은 재사용 가능하나 no-public-GT 메타데이터를 `V4-CM-0.4`에서 추가 검증 |
| `CM-0.4` | 구 16-table manifest·source preflight | 구 epoch manifest와 단일 expected hash/profile은 신규 기준으로 사용하지 않음. source/corrected file과 runtime/evaluation bootstrap profile을 분리 지원하는지 재검토한 코드만 `V4-CM-1.1·1.7·2.7` 증빙으로 연결 |
| `C-0a`, 구 `C-0.x` | 구 스키마 ALTER·backfill·checkpoint·Tool runtime | 구 데이터 전용 SQL·fixture는 폐기 또는 이력 보존. clean CREATE migration과 신규 Runtime 계약은 `V4-CM-2.*`, `V4-C-0.*`에서 재검증 |

## 2. 공수 요약

| 영역 | 담당 | 공수 | 핵심 산출물 |
|---|---|---:|---|
| Common | 4명 공동, 통합 관리 C | 32.5h | corrected source·clean bootstrap·profile별 migration·통합 검증 |
| A Detection | 신동원 | 42.5h | 결정론적 재계산·규칙 알람·비지도 score·incident model signal·synthetic 평가·Detection 화면 |
| B Knowledge | 강연권 | 35.5h | corrected RAG·Neo4j·Tool·Knowledge 화면·검색 평가 |
| C Agent/HITL | 방대혁 | 62.0h | 근거 기반 가설·LangGraph·base 조치·anomaly gate·HITL·email·MES Mock |
| D Analytics | 천승현 | 40.5h | 신규 스키마 Text2SQL·방어·versioned 회귀 질문·Analytics·감사 화면 |
| **합계** | | **213.0h** | P2 도전 과제 제외 |

C가 가장 무거운 구조는 기존 팀 합의다. Common은 C의 개인 공수가 아니라 전원 공동 작업으로 산정한다.
우선순위별 공수는 **P0 97.0h / P1 116.0h**이며 P2는 합계에서 제외한다.

## 3. Common — 신규 데이터·Runtime·통합

### V4-CM-0. 기획·계약 동결

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-CM-0.1 | P0 | 신규 data epoch 등록. 완료: 문서·AI 지침에 구 데이터 정답 사용 금지 배너, source ZIP SHA-256·수신일·파일 목록 기록 | FR-I-01 | 없음 | 1.0h |
| V4-CM-0.2 | P0 | 공통 Enum·ID 계약 개정. 완료: `AlarmRef.source`, 3단계 Action, channel, `UNKNOWN`을 포함한 DeliveryStatus, `ThresholdValidationStatus`, structured nullable `AnomalySignal`, versioned `IncidentModelSignal`이 코드·계약 테스트에서 동일하며 Runtime synthetic label 필드는 0건이다 | FR-I-01, FR-I-07 | V4-CM-0.1 | 1.5h |
| V4-CM-0.3 | P0 | API·Tool 영향표. 완료: 기존 DTO/endpoint별 유지·변경·폐기·adapter 목록과 소유자 확정 | FR-I-01, FR-I-07 | V4-CM-0.2 | 1.5h |
| V4-CM-0.4 | P1 | 완료 증빙 규칙 갱신. 완료: `ground_truth_available`·`label_source`·`production_ground_truth_available`·`usage_scope`·source/generator/model revision을 평가 artifact 필수 메타데이터로 지정하고 synthetic 지표를 실제 공정 성능으로 표기하지 못하게 한다 | 요구사항 13장 | V4-CM-0.1 | 1.0h |

### V4-CM-1. Source correction과 공용 DB bootstrap

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-CM-1.1 | P0 | manifest profile 분리. 완료: `format_version=3`, `artifact_type`, `dataset_epoch`, `correction_version`, `hash_algorithm`, DB `profile`을 필수 기록한다. source/corrected file manifest에는 제공 `action_history` CSV 48건을 Mock/reference로 보존하고, `kosa_agent`·`kosa_agent_e2e` runtime과 `kosa_text2sql` evaluation bootstrap profile manifest는 별도 expected row/hash를 사용한다. synthetic gold는 DB profile과 분리한 `EVALUATION_ONLY` artifact type으로 정의하며 단일 expected hash 공유 0건, 비밀정보 0건 | FR-I-04 | V4-CM-0.1 | 1.0h |
| V4-CM-1.2 | P0 | corrected generator·copy 파이프라인. 완료: 원본 ZIP·Generator write 0회, 별도 corrected generator revision·hash 기록, 임시 디렉터리 생성→검증→원자 교체, 재실행 동일 hash | FR-I-04 | V4-CM-1.1 | 2.0h |
| V4-CM-1.3 | P0 | Trace 순번 보정. 완료: `[팀 잠정] seq_no=ordv`, Step 1은 0~2·Step 2는 3~5, 기존 PK 유지, 중복 PK 0건 | FR-A-01, FR-I-04 | V4-CM-1.2 | 1.5h |
| V4-CM-1.4 | P0 | 누락 seed·시간 overlay. 완료: `dim_parameter` 8행, Summary 알람 시각은 대응 track-in, metrology 시각은 NULL 유지, 변환 근거 기록 | FR-A-01, FR-I-04 | V4-CM-1.2 | 1.0h |
| V4-CM-1.5 | P0 | PostgreSQL base schema bootstrap. 완료: allowlist host·DB guard 후 `kosa_agent`·`kosa_agent_e2e`·`kosa_text2sql`에 base table 9개 schema만 생성하고 세 DB의 `action_history` 0행·transaction rollback·success marker를 검증한다 | FR-I-04 | V4-CM-1.1 | 1.0h |
| V4-CM-1.6 | P0 | Neo4j destructive-safe loader. 완료: raw `MATCH (n) DETACH DELETE n` 직접 실행과 populated graph 기본 교체를 금지한다. empty/fresh target 또는 fingerprint 확인·팀 공유·백업 후 `--replace --confirm`일 때만 교체하며 38 nodes/81 relationships·복구 절차를 검증한다 | FR-I-04, FR-B-01 | V4-CM-1.1 | 1.5h |
| V4-CM-1.7 | P0 | source·base schema verifier. 완료: source/corrected file의 Mock action 48건과 세 PostgreSQL base schema의 action 0건을 구분하고 profile 간 hash를 동일값으로 강제하지 않는다. corrected file PK/FK·reference output, Neo4j 38/81, DB write 대상 구분을 확인한다 | FR-I-04 | V4-CM-1.3, V4-CM-1.4, V4-CM-1.5, V4-CM-1.6 | 1.5h |

### V4-CM-2. Profile별 reference·Runtime CREATE migration

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-CM-2.1 | P0 | `001_reference_extensions.sql`. 완료: `kosa_agent`·`kosa_agent_e2e`·`kosa_text2sql` 모두에 R03·document corpus·`nl_query_log`·`v_alarm_event`를 CREATE하고 재실행 안전, profile별 기존 action 상태 불변, 구 DB backfill 의존 0건 | FR-A-06, FR-B-02, FR-D-05, FR-I-04 | V4-CM-0.2, V4-CM-1.7 | 2.0h |
| V4-CM-2.2 | P0 | profile별 corrected base data 채택·적재. 완료: `001` 성공 뒤 `kosa_agent`·`kosa_text2sql`은 기적재 corrected base를 채택하고 `dim_parameter.parameter_name` 3건(`ET_ESC`·`PH_DEV`·`PH_PEB`)을 corrected 값으로 보정하며, `kosa_agent_e2e`는 신규 적재한다. 최종 action 0/0/48을 유지하고 PK/FK·reference output, 재실행·rollback을 검증한다. fresh bootstrap 결과 자체는 상위 요구사항·설계서와 동일하며 기적재 DB의 adoption 경로만 추가한다 | FR-I-04 | V4-CM-2.1 | 1.0h |
| V4-CM-2.3 | P0 | evaluation Mock fixture 채택·등록. 완료: corrected base 채택 뒤 `kosa_text2sql`의 기존 `action_history` 48건이 제공본과 일치함을 검증하고(중복 INSERT 0건) profile metadata에 `fixture_type=MOCK`을 기록한다. 비어 있는 경우에만 48건을 적재한다. 이는 DB 컬럼이 아니며 Text2SQL·화면 계약 회귀에만 사용하고 Agent/Fault 정답·seed로 사용하지 않는다 | FR-D-08, FR-I-04 | V4-CM-2.2 | 1.0h |
| V4-CM-2.4 | P0 | `002_agent_runtime_clean.sql`. 완료: corrected base 적재 뒤 `kosa_agent`·`kosa_agent_e2e`에만 agent run/tool/alarm link/action/approval/audit/channel delivery와 active incident·`(action_id,channel)` 제약을 CREATE, action 0건 guard, `kosa_text2sql` 적용 거부·양성/음성 test | FR-C-04~09, FR-I-04 | V4-CM-2.2 | 2.0h |
| V4-CM-2.4.1 | P0 | `003_agent_run_severity_pair.sql` hotfix. 완료: `agent_run`의 `action`·`severity` 반쪽 행(`action='WARNING', severity=NULL` 등)을 PostgreSQL 3값 논리 구멍 없이 차단하는 명명 CHECK를 runtime 2개 DB에만 ADD한다. `002` 원본은 수정하지 않고 successor stage `runtime_guarded` manifest·marker를 발급하며, 002 runner의 fresh 적용 경로(제약 19개)를 깨지 않도록 stage별 기대 signature를 분리한다. `kosa_text2sql` 적용 거부·재실행 no-op·rollback을 검증한다 | FR-C-03, FR-C-07, FR-I-04 | V4-CM-2.4 | 1.0h |
| V4-CM-2.5 | P0 | Checkpoint 초기화. 완료: 두 runtime DB에서만 별도 one-shot, 재실행 안전, 앱 시작 자동 setup 0회, thread 재개 smoke | FR-C-04 | V4-CM-2.4.1 | 1.0h |
| V4-CM-2.6 | P0 | 최소권한 role·pool 계약. 완료: profile별 app/readonly/logger/delivery 허용·거부 matrix, 생성 SQL을 writer로 실행 0회 | NFR-01, FR-D-03 | V4-CM-2.4 | 1.0h |
| V4-CM-2.7 | P0 | profile migration 검증기. 완료: `001`과 corrected base는 3개 DB, evaluation Mock fixture는 `kosa_text2sql`에만, `002`·checkpoint는 runtime 2개 DB에만 존재함을 검증한다. 최종 action 상태 0/0/48, fixture metadata, 적용 순서·expected table/column/index/check·idempotency·비밀 출력 0건을 확인하고, 누적 `nl_query_log`는 immutable content hash에서 제외해 schema·권한과 별도 artifact로 검증한다 | FR-I-04 | V4-CM-2.3, V4-CM-2.4, V4-CM-2.4.1, V4-CM-2.5, V4-CM-2.6 | 1.5h |

### V4-CM-3. 애플리케이션·배포·최종 검증

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-CM-3.1 | P1 | Backend 통합. 완료: A/B/C/D Router·오류 계약·OpenAPI와 API 명세 일치, `/health`·`/health/ready`는 업무 API 수에서 제외하고 기능별 503을 격리한다. ready는 PostgreSQL·Neo4j·n8n을 병렬 timeout으로 검사하며 Neo4j 38/81 success marker와 ACTIVE corpus revision을 확인한다 | FR-I-01, FR-I-05, FR-I-07 | V4-A-5.4, V4-B-5.3, V4-C-7.1, V4-D-5.3, V4-D-6.1, V4-D-6.3 | 1.5h |
| V4-CM-3.2 | P1 | React 통합. 완료: 신규 5개 기능 영역을 기존 8개 Page 컴포넌트에 매핑하고 상세·legacy redirect URL을 별도 유지, direct/deep link 통과 | FR-I-02, FR-I-03 | V4-A-6.4, V4-B-6.3, V4-C-9.3, V4-D-8.2 | 2.0h |
| V4-CM-3.3 | P1 | Compose/Nginx 통합. 완료: FastAPI·React·PostgreSQL·Neo4j·n8n, `/api` proxy, 명시적 CORS Origin·service별 env allowlist, 고정 image/version | FR-I-04, FR-I-06 | V4-CM-2.7, V4-CM-3.1, V4-CM-3.2 | 1.5h |
| V4-CM-3.4 | P0 | 공용 DB Runtime reset guard. 완료: host·DB·token 확인 후 `kosa_agent_e2e`의 실행 데이터만 초기화한다. `kosa_agent`·`kosa_text2sql` 대상은 거부하고 source/reference/corpus/checkpoint schema를 보존한다 | 요구사항 13장 | V4-CM-2.7 | 1.5h |
| V4-CM-3.5 | P1 | 최종 검증·문서화. 완료: pytest·ruff·npm lint/build·통합 E2E, source hash 불변, synthetic artifact의 Runtime·Agent·Text2SQL·RAG 유입 0건, 결과서·실행가이드와 미확정 정책 목록 | 요구사항 13장 | V4-CM-3.3, V4-CM-3.4, V4-A-7.2, V4-B-8.2, V4-C-10.2, V4-D-9.2 | 1.0h |

**Common 합계: 32.5h**

## 4. A — Detection

### V4-A-0. 신규 기준선

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-0.1 | P0 | corrected source profile 검증. 완료: Trace·summary·evaluation·알람 파일의 row/schema/hash 일치, 공용 DB write 0회 | FR-A-01, FR-A-02 | V4-CM-1.7 | 1.0h |
| V4-A-0.2 | P0 | 계산 규칙 fixture 동결. 완료: parameter 한계·window·CL±3σ·R03 정책을 versioned table로 기록, 멘토 확인값과 잠정값 구분 | FR-A-01, FR-A-02 | V4-A-0.1 | 1.0h |

### V4-A-1. Trace 요약 재계산

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-1.1 | P0 | 그룹 집계 순수 함수. 완료: `(lot_hist_id, parameter_id, recipe_step_no)` mean·std·min·max·count 결정론 재현 | FR-A-01 | V4-A-0.2 | 1.5h |
| V4-A-1.2 | P0 | parameter 규격 판정. 완료: 상·하한·단측 규칙과 null 경계 fixture 통과 | FR-A-01 | V4-A-1.1 | 1.5h |
| V4-A-1.3 | P0 | summary reference 비교. 완료: 4,800건의 PK·count 완전 일치와 수치 절대오차 0.001 이하, 원본/공용 source write 0회 | FR-A-01 | V4-A-1.2 | 1.0h |
| V4-A-1.4 | P1 | 재계산 Service·Repository. 완료: batch/단건 같은 순수 계산 사용, timeout·dependency 오류 계약, corrected runtime profile 실제 조회 | FR-A-01 | V4-A-1.3, V4-CM-2.2 | 1.5h |

### V4-A-2. Evaluation·알람 규칙

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-2.1 | P0 | window evaluation 구현. 완료: 신규 가이드 식으로 4,800건(IN 4,542/OOC 216/OOS 42) reference output 결정론 비교 | FR-A-02 | V4-A-1.3 | 1.5h |
| V4-A-2.2 | P0 | Trace 알람 구현. 완료: raw point OOS 후보·발행 키·시각·중복 제거와 TRACE 126건 reference 일치 | FR-A-02 | V4-A-2.1 | 1.5h |
| V4-A-2.3 | P0 | Summary 알람 구현. 완료: 비OOS 표본 mean 분포의 CL±3σ와 Summary mean 판정, 보정 시각 포함 SUMMARY 47건 reference 일치 | FR-A-02 | V4-A-2.1 | 1.5h |
| V4-A-2.4 | P0 | R03 구현·적재. 완료: `(chamber, parameter, recipe step)`, 비OOS reset, LOT 경계 유지, `run==3` 1회 발행 규칙으로 corrected source를 계산한다. `source`·`lot_hist_id`·`parameter_id`·`recipe_step_no`·`policy_version` canonical JSON의 SHA-256 앞 20 lowercase hex로 `R03-<20hex>`를 만들고 같은 source·policy rebuild에서 ID 불변을 검증한다. 전용 transaction에서 `r03_alarm_history`를 rebuild해 원자 교체하고 3건·`member_refs`·`policy_version`·hash 충돌 0건을 확인하며 실패 시 이전 상태로 rollback한다 | FR-A-02 | V4-A-2.1, V4-CM-2.1 | 1.0h |

### V4-A-3. 비지도 이상 점수

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-3.1 | P0 | feature 계약. 완료: Fault·metrology·Generator 주입 정보 제외, feature order·결측 대체·coverage 고정 | FR-A-03 | V4-A-1.3 | 2.0h |
| V4-A-3.2 | P0 | LOT 분리 학습. 완료: train/evaluation LOT 중복 0, seed·library·hyperparameter manifest | FR-A-03 | V4-A-3.1 | 1.5h |
| V4-A-3.3 | P0 | score 정규화·artifact. 완료: 0~1 방향·model version·score method·선택 threshold와 기본 `UNVERIFIED` 상태 저장, 같은 bundle 재현 | FR-A-04 | V4-A-3.2 | 1.5h |
| V4-A-3.4 | P1 | 라벨 없는 평가. 완료: score 분포·상위 K·선택 비율·규칙/계측 연관 분석, supervised 분류 성능 계산 0건 | FR-A-03, FR-A-04 | V4-A-3.3 | 1.0h |
| V4-A-3.5 | P1 | synthetic evaluation gold 생성. 완료: corrected generator에서 별도 artifact를 결정론적으로 생성하고 `ground_truth_available=true`, `label_source=SYNTHETIC_GENERATOR`, `production_ground_truth_available=false`, `usage_scope=EVALUATION_ONLY`, generator revision·seed·hash를 기록한다. 원본·corrected DB·학습 feature·Runtime·Agent·Text2SQL·Neo4j·RAG write 0회 | FR-A-03, FR-A-04 | V4-CM-1.2, V4-A-0.2 | 1.5h |
| V4-A-3.6 | P1 | action-threshold 검증 artifact. 완료: LOT 분리된 synthetic evaluation protocol로 후보 `action_threshold`를 검증하고 통과한 bundle만 `VERIFIED`로 서명한다. 지표는 synthetic generator agreement로 표기하며 기준 미충족·revision/hash 불일치는 `UNVERIFIED`로 fail closed한다 | FR-A-03, FR-A-04 | V4-A-3.3, V4-A-3.5 | 1.5h |

### V4-A-4. Tool·계약

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-4.1 | P1 | 모델 비의존 Summary 조회 Service. 완료: `lot_hist_id`로 wafer·parameter summary와 nullable `AnomalySignal`을 반환한다. 모델·threshold artifact가 미준비면 summary는 정상 반환하고 `AnomalySignal=null`로 완료하며 synthetic label 원문은 반환하지 않는다. AlarmRef 해석은 C resolver에 위임 | FR-A-05 | V4-A-1.4 | 1.5h |
| V4-A-4.2 | P1 | Tool 계약. 완료: 정상·NOT_FOUND·TIMEOUT·DEPENDENCY_ERROR와 nullable structured `AnomalySignal`을 고정한다. 모델·threshold 미준비는 Tool 전체 오류가 아니며 규칙 summary와 `AnomalySignal=null`을 반환하고 synthetic label 필드 노출은 0건이다 | FR-A-05 | V4-CM-0.2, V4-A-4.1 | 1.5h |
| V4-A-4.3 | P1 | C wrapper contract fixture. 완료: C가 Tool 호출 이력·latency를 기록하고 A 반환 DTO 재정의 0건 | FR-A-05, FR-C-08 | V4-A-4.2, V4-C-2.2 | 1.0h |
| V4-A-4.4 | P1 | 검증된 anomaly signal population. 완료: V4-A-3.6의 bundle을 명시적으로 로드해 `score`·`model_version`·`score_method`·optional `display_threshold`/`is_anomaly`/`action_threshold`·`threshold_version`·`threshold_validation_status`를 채운다. bundle 없음·hash/revision 불일치는 base summary를 유지하고 null 또는 `UNVERIFIED`로 fail closed하며 synthetic label 원문은 반환하지 않는다 | FR-A-04, FR-A-05 | V4-A-3.6, V4-A-4.2 | 1.0h |
| V4-A-4.5 | P1 | incident model signal batch 집계. 완료: `build_incident_model_signal(lot_hist_ids, action_policy_version)`가 중복 제거·안정 정렬한 member를 단일 batch로 조회한다. 공통 `IncidentModelSignal`은 enabled, `READY\|DISABLED\|UNAVAILABLE`, incident score, `display_threshold`·`action_threshold`, expected/valid member·max score member, 동일 `model_version`·`score_method`·`threshold_version`, allowlisted `action_policy_version`, reason을 포함한다. VERIFIED·coverage 100%·provenance 동질성이 깨지면 `UNAVAILABLE`로 fail closed하고 member별 Tool 호출·synthetic label 조회는 0건이다 | FR-A-05, FR-C-03 | V4-A-4.4 | 1.5h |

### V4-A-5. Detection API

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-5.1 | P1 | 알람 목록·상세 API. 완료: `date_from`·`date_to`·`area`(허용값 `photo`, `etch`, `ALL`)는 필수, equipment·chamber·parameter는 선택 필터다. corrected 전체 기간 `2026-08-01~2026-08-12`와 `area=ALL`에서 기본 TRACE 126+SUMMARY 47=`total=173`, 같은 필터의 `source=R03`은 3건, `include_derived=true`면 `total=176`; 단건은 `GET /alarms/{source}/{alarm_id}`, 안정 정렬·404/422 | FR-A-06 | V4-A-2.4, V4-CM-2.2 | 1.5h |
| V4-A-5.2 | P1 | parameter·Trace API. 완료: `GET /parameters`, 최소 `GET /trace`, 확장 catalog/search가 corrected seq 0~5·step·한계선에서 같은 결과를 반환 | FR-A-06 | V4-A-1.4 | 1.5h |
| V4-A-5.3 | P1 | dataset bounds·대시보드 API. 완료: `GET /dataset/bounds`가 dataset epoch/revision, min/max date `2026-08-01~2026-08-12`, area·equipment·chamber·parameter 선택지를 반환한다. Frontend는 min/max를 `date_from`·`date_to`와 `area=ALL`로 명시 전송하며 trend·parameter·chamber 집계가 같은 필터를 사용한다. 필터 결과 0건도 `date_range=[date_from,date_to]`, count 0, 빈 목록으로 응답하고 `date_range=[]`은 거부하며 LLM 호출은 0회다 | FR-A-06 | V4-A-5.1 | 1.5h |
| V4-A-5.4 | P1 | C 승인 목록 결합. 완료: C ApprovalService 직접 재사용, A SQL/HTTP self-call 0건 | FR-A-06 | V4-A-5.3, V4-C-7.1 | 1.0h |

### V4-A-6. Detection React

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-6.1 | P1 | 대시보드 실연동. 완료: 첫 진입에 `GET /dataset/bounds`를 호출해 초기 `date_from`·`date_to`·`area=ALL`을 명시 전송하고 line trend·bar comparison·필터·적용 기간·실데이터 표시 | FR-A-07 | V4-A-5.3, V4-A-5.4 | 2.0h |
| V4-A-6.2 | P1 | 알람 목록·상세. 완료: source 표시·Agent 실행 연결, 분석 실행은 `POST /agent/runs`에 `{alarm:{source,alarm_id}}`만 전송하고 legacy `{alarm_id}` payload는 0건이다. `/alarms/:alarmId`의 `:alarmId`를 `TRACE:TAL-...`·`SUMMARY:SAL-...`·`R03:R03-<20hex>` composite token으로 직렬화해 direct URL에서 AlarmRef를 복원하며 source 없는 legacy ID는 선택을 요구하고 Mock은 0건이다 | FR-A-07, FR-C-13 | V4-A-5.1, V4-C-7.1 | 1.5h |
| V4-A-6.3 | P1 | Trace viewer. 완료: seq 0~5·step 구분·한계선·다중 series, Loading/Error/Empty/Success | FR-A-07 | V4-A-5.2 | 1.5h |
| V4-A-6.4 | P1 | Frontend 계약 테스트. 완료: query alias·enum 임의 변환 0건, build·route·empty fixture 통과 | FR-A-07 | V4-A-6.1, V4-A-6.2, V4-A-6.3 | 1.0h |

### V4-A-7. Detection 통합 검증

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-A-7.1 | P1 | 계산·API·UI 통합. 완료: 기본 UI 저장 알람 173건과 파생 R03 표기를 구분하되 Agent incident에는 R03 포함, 같은 AlarmRef가 DB·API·React·Agent에서 일치하고 model 미준비 시 null signal 경로도 정상 동작한다 | FR-A-01~07 | V4-A-4.3, V4-A-5.4, V4-A-6.4, V4-C-2.3 | 1.5h |
| V4-A-7.2 | P1 | Detection 평가 artifact. 완료: 실제 GT 없음과 synthetic 평가를 분리하고 source/generator/model/threshold revision·`label_source`·`production_ground_truth_available`·규칙 diff·비지도 분석·synthetic agreement·incident signal 집계 검증·미확정 정책을 기록 | FR-A-03, FR-A-04, FR-A-05 | V4-A-3.4, V4-A-4.5, V4-A-7.1 | 1.0h |

**A 합계: 42.5h**

## 5. B — Knowledge

### V4-B-0. 신규 관계·문서 기준선

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-0.1 | P0 | Neo4j 기준 검증. 완료: 38 nodes·81 relationships, 식별자·방향·중복·필수 속성 보고서 | FR-B-01 | V4-CM-1.7 | 1.0h |
| V4-B-0.2 | P0 | 구 RAG 충돌 audit. 완료: R02/R03·상하류·4단계 조치·구 ID 표현의 수정 목록과 출처 절 기록 | FR-B-02 | V4-CM-0.1 | 1.0h |
| V4-B-0.3 | P0 | corpus provenance 계약. 완료: raw/corrected SHA, 수정 사유, chunk·embedding revision schema 확정 | FR-B-02, FR-B-05 | V4-B-0.2 | 1.5h |

### V4-B-1. Corrected RAG corpus

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-1.1 | P0 | corrected 문서 작성. 완료: 신규 가이드와 일치, 추측은 `[팀 잠정]`·`[멘토 확인]` 표시, 원본 수정 0회 | FR-B-02 | V4-B-0.2 | 2.0h |
| V4-B-1.2 | P0 | corpus 교차 리뷰. 완료: A가 Detection 규칙, C가 action/HITL 표현을 검토하고 unresolved 목록 분리 | FR-B-02 | V4-B-1.1, V4-A-0.2, V4-CM-0.2 | 1.5h |
| V4-B-1.3 | P0 | chunk·embedding manifest. 완료: 결정론적 chunk ID·길이·중복 검사, model revision·dimension·NULL 0건 | FR-B-02, FR-B-05 | V4-B-0.3, V4-B-1.2 | 1.0h |
| V4-B-1.4 | P0 | corrected corpus idempotent loader. 완료: `001_reference_extensions.sql` 적용 후 revision별 stage→검증→원자 swap, 같은 revision 재실행 중복 0건, 실패 시 이전 active corpus rollback·복구 | FR-B-02, FR-B-05, FR-I-04 | V4-B-1.3, V4-CM-2.1 | 1.5h |

### V4-B-2. Neo4j 관계 조회

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-2.1 | P0 | 챔버·설비 Cypher. 완료: parameter binding, area/step/parameter/sibling DTO, 안정 정렬 | FR-B-03 | V4-B-0.1 | 1.5h |
| V4-B-2.2 | P0 | 상하류 관계 Cypher. 완료: 신규 master 방향을 독립 fixture와 비교, 경계 관계 없음 처리 | FR-B-03 | V4-B-2.1 | 1.5h |
| V4-B-2.3 | P1 | Neo4j Repository/Service. 완료: session lifecycle·timeout·not found·dependency error 매핑, string Cypher 조합 0건, canonical directed tuple `type\|from_label:id\|to_label:id`로 stable `relation_id`를 생성한다. `graph_revision`은 별도 provenance로 반환하고 같은 business edge의 ID는 revision 변경에도 유지한다 | FR-B-03 | V4-B-2.1, V4-B-2.2 | 1.0h |

### V4-B-3. 문서 검색

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-3.1 | P0 | embedding singleton·revision guard. 완료: 동시 생성 1회, runtime download 0회, corpus mismatch는 MODEL_NOT_READY | FR-B-05 | V4-B-1.4 | 1.5h |
| V4-B-3.2 | P0 | pgvector 검색. 완료: 1024차원·score 방향·top_k 1~10·model filter·결정론 정렬 | FR-B-04 | V4-B-3.1 | 1.5h |
| V4-B-3.3 | P1 | 검색 결과 provenance. 완료: document/chunk/corpus revision과 실제 content, 0건은 정상 empty | FR-B-04 | V4-B-3.2 | 1.0h |

### V4-B-4. Knowledge Tool

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-4.1 | P1 | 관계 Tool. 완료: 정상·NOT_FOUND·TIMEOUT·DEPENDENCY_ERROR, relation ID 포함 | FR-B-03 | V4-B-2.3, V4-CM-0.2 | 1.5h |
| V4-B-4.2 | P1 | 문서 Tool. 완료: 0건 성공, MODEL_NOT_READY·TIMEOUT·DEPENDENCY_ERROR, chunk ID·revision 포함 | FR-B-04 | V4-B-3.3, V4-CM-0.2 | 1.0h |
| V4-B-4.3 | P1 | C wrapper contract. 완료: C가 호출 로그를 기록하고 B DTO·정렬을 재해석하지 않음 | FR-B-03, FR-B-04 | V4-B-4.1, V4-B-4.2, V4-C-2.2 | 1.0h |

### V4-B-5. Knowledge API

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-5.1 | P1 | 관계 API. 완료: `GET /relations/chambers/{chamber_id}`·`GET /relations/equipment/{equipment_id}`, 200/404/422/503 계약, OpenAPI DTO | FR-B-06 | V4-B-2.3 | 1.5h |
| V4-B-5.2 | P1 | 문서 검색·상세 API. 완료: `POST /documents/search`·`GET /documents/{document_id}`, 전체 hit 필드·chunk 정렬·빈 결과·corpus mismatch 계약 | FR-B-06 | V4-B-3.3 | 1.0h |
| V4-B-5.3 | P1 | Contract test. 완료: stable `relation_id`와 별도 `graph_revision`, `DocumentHit.corpus_revision` 필수·문서/chunk ID 대응, extra forbid·nullability·정렬·오류 본문 검증 | FR-B-06 | V4-B-5.1, V4-B-5.2 | 1.0h |

### V4-B-6. Knowledge React

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-6.1 | P1 | 관계 화면 실연동. 완료: 장비·챔버 선택, 상하류·parameter 표시, direct URL 복원 | FR-B-06 | V4-B-5.1 | 2.0h |
| V4-B-6.2 | P1 | 문서 검색·근거 화면. 완료: score·chunk·corpus revision·content, Agent deep link | FR-B-06 | V4-B-5.2, V4-C-7.1 | 1.5h |
| V4-B-6.3 | P1 | 상태·접근성. 완료: Loading/Error/Empty/Success·키보드 선택·Mock 0건 | FR-B-06, NFR-17 | V4-B-6.1, V4-B-6.2 | 1.0h |

### V4-B-7. Knowledge 평가

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-7.1 | P1 | 독립 관계 fixture. 완료: master에서 독립 산출한 양성·음성·방향·경계 질의 전수 통과 | FR-B-07 | V4-B-2.3 | 1.5h |
| V4-B-7.2 | P1 | 검색 질문셋. 완료: corrected corpus에서 10문항 이상, 복수정답·expected chunk·질문 작성자 기록 | FR-B-07 | V4-B-1.3 | 1.5h |
| V4-B-7.3 | P1 | Retrieval artifact. 완료: Recall@4·MRR·실패 사례·model/corpus revision·fixture hash | FR-B-07 | V4-B-3.3, V4-B-7.2 | 1.0h |

### V4-B-8. Agent 근거 통합

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-B-8.1 | P1 | 근거 참조 무결성. 완료: Agent relation/chunk ID가 동일 run Tool 결과에 존재, stale revision 거부 | FR-C-01, FR-C-03 | V4-B-4.3, V4-C-3.2 | 1.5h |
| V4-B-8.2 | P1 | Knowledge 결과 문서화. 완료: 평가·통합 테스트·미확정 RAG 항목과 재임베딩 절차 기록 | FR-B-07 | V4-B-7.3, V4-B-8.1 | 1.0h |

**B 합계: 35.5h**

## 6. C — Agent·HITL·n8n

### V4-C-0. Runtime 계약

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-0.1 | P0 | Agent DTO·Enum 개정. 완료: AlarmRef·predicted hypothesis·3단계 action·channel delivery·review label·공통 `IncidentModelSignal`을 사용하고 WAFER `AnomalySignal`·synthetic label을 조치 판정 payload로 직접 받는 필드는 0건 | FR-C-01, FR-C-03, FR-C-06, FR-C-07 | V4-CM-0.2 | 1.0h |
| V4-C-0.2 | P0 | Runtime schema 검토·Repository 골격. 완료: `002_agent_runtime_clean.sql`과 모델 일치, 구 alarm/action FK 가정 0건 | FR-C-04~09 | V4-CM-2.4 | 2.0h |
| V4-C-0.3 | P0 | ID·감사 계약. 완료: run/action/approval/tool/delivery ID와 append-only 이벤트·entity map 테스트 | FR-C-08, NFR-05 | V4-C-0.1, V4-CM-2.4 | 1.5h |
| V4-C-0.4 | P0 | Checkpoint·thread 계약. 완료: run ID와 독립 thread UUID, 저장·interrupt·동일 thread 재개 fixture | FR-C-04 | V4-CM-2.5, V4-C-0.2 | 1.0h |

### V4-C-1. AlarmRef·incident

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-1.1 | P0 | AlarmRef resolver. 완료: TRACE/SUMMARY/R03별 lot·wafer·chamber·time 문맥, 없는/모호한 참조 안전 실패 | FR-C-01, FR-C-09 | V4-A-2.4, V4-C-0.2 | 2.0h |
| V4-C-1.2 | P0 | incident 집계. 완료: `[팀 잠정] (lot_id,chamber_id)`, 저장 알람과 파생 R03를 포함해 incident 10개, 대표 ref와 전체 refs 안정 정렬, action Mock 미사용 | FR-C-09, FR-C-14 | V4-C-1.1 | 1.5h |
| V4-C-1.3 | P0 | 중복 실행 방지. 완료: 동일 incident 동시 요청 1개만 활성, 처리 완료 재배치 신규 run 0건 | FR-C-09 | V4-C-1.2, V4-CM-2.4 | 1.5h |
| V4-C-1.4 | P0 | lot route Repository·consistency. 완료: 입력 AlarmRef에서 resolve한 `lot_id`+`wafer_no` 범위의 `lot_history`만 공정 순서로 조회해 실제 전·후 Process Step과 근거 `lot_hist_id`를 반환한다. 다른 wafer를 근거로 쓸 때는 해당 AlarmRef를 별도로 resolve·조회하며, Neo4j의 고정 설비 upstream을 실제 routing으로 오인하지 않는 양성·음성 fixture를 통과한다 | FR-C-10 | V4-C-1.1 | 1.5h |

### V4-C-2. Tool wrapper·예산

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-2.1 | P0 | Tool 호출 예약·종료 갱신. 완료: SUCCESS/ERROR/TIMEOUT/crash가 같은 행, latency·input/output 기록 | FR-C-08 | V4-C-0.2 | 1.5h |
| V4-C-2.2 | P0 | 영속 예산. 완료: 총 8회·동일 Tool 최대 4회·send 예약, HITL/복구 후 count 초기화 0건 | FR-C-08 | V4-C-2.1 | 2.0h |
| V4-C-2.3 | P1 | A·B Tool adapter. 완료: 반환 DTO 재정의 없이 State에 evidence와 failure reason 축적 | FR-C-02, FR-C-03 | V4-A-4.2, V4-B-4.1, V4-B-4.2, V4-C-2.2 | 1.5h |
| V4-C-2.4 | P1 | 근거 ID validator. 완료: 주장별 AlarmRef·parameter·metrology와 `lot_hist_id`·`relation_id`·`graph_revision`이 실제 입력·Tool 결과에 존재하고, 문서 근거를 사용하면 `chunk_id`·`corpus_revision`도 ACTIVE 결과와 일치한다 | FR-C-01, FR-C-10, FR-C-15 | V4-C-1.4, V4-C-2.3 | 1.5h |

### V4-C-3. LangGraph·원인 가설

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-3.1 | P1 | State·Node·Edge. 완료: Level 1 고정 경로와 Level 2 조건 분기, 같은 Tool/State·approval gate 사용 | FR-C-02 | V4-C-1.2, V4-C-2.3 | 2.0h |
| V4-C-3.2 | P1 | 근거 수집 graph. 완료: 요약→관계→문서와 실제 lot route consistency를 수집하고 부족 근거 추가 조회, 예산 소진 안전 종료 | FR-C-02, FR-C-03, FR-C-10 | V4-C-1.4, V4-C-3.1 | 1.5h |
| V4-C-3.3 | P1 | 구조화 가설 생성. 완료: FOC/RFM/MFD/TMD/OTH, confidence·limitations·evidence, 정답 확정 표현 금지 | FR-C-01 | V4-C-2.4, V4-C-3.2 | 1.5h |
| V4-C-3.4 | P1 | 파싱 교정·모델 기록. 완료: 최초/1회 교정 성공·실패, provider/model/prompt/temperature/token/latency 기록 | FR-C-01, FR-C-02, FR-C-07 | V4-C-3.3 | 1.0h |

### V4-C-4. 라벨 없는 Agent 평가

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-4.1 | P1 | 평가 rubric. 완료: 구조·근거 존재·근거 충실성·불확실성·조치 일관성 정의, 분류 성능 항목 없음 | FR-C-01, FR-C-15 | V4-CM-0.4 | 1.5h |
| V4-C-4.2 | P1 | 자동 평가 runner. 완료: schema/ID/consistency/중복 지표와 ground truth false metadata | FR-C-01, FR-C-02, FR-C-15 | V4-C-3.4, V4-C-4.1 | 1.5h |
| V4-C-4.3 | P1 | 블라인드 리뷰셋 형식. 완료: 표본·평가자·rubric version·reviewed label optional 분리 | FR-C-01, FR-C-15 | V4-C-4.1 | 1.5h |

### V4-C-5. 모델 비의존 base 조치와 2단계 anomaly gate

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-5.1 | P0 | 모델 비의존 base policy 순수 함수. 완료: Summary OOC→MONITORING, Trace OOS→WARNING, R03→EQP_HOLD의 우선순위를 LLM·DB·anomaly score·threshold 없이 결정하고 R03 없는 EQP_HOLD 0건을 보장한다 | FR-C-03 | V4-A-2.4, V4-C-1.2 | 2.0h |
| V4-C-5.2 | P0 | base 조치 fixture. 완료: 양성·음성·복수 source·R03 경계·정책 교체와 corrected 기본 회귀 10 incident=5/2/3을 모델 없이 통과한다. 결과는 실제 조치 Gold가 아니다 | FR-C-03, FR-C-14 | V4-C-5.1 | 2.0h |
| V4-C-5.3 | P1 | action 생성 Service. 완료: incident당 유효 action 1건, 제공 Mock action 참조 0건, reason/evidence 보존 | FR-C-03, FR-C-14 | V4-C-5.2, V4-CM-2.4 | 1.5h |
| V4-C-5.4 | P1 | incident model gate policy decorator 통합. 완료: C는 A의 공통 `IncidentModelSignal`만 입력으로 받고 WAFER `AnomalySignal`·synthetic label을 직접 판정하지 않는다. `[팀 잠정]` enabled·`status=READY`·coverage 100%·동일 model/score/display/action-threshold provenance·allowlisted policy·`incident_score >= action_threshold`·SUMMARY-only MONITORING일 때만 WARNING 상향을 주입한다. base 하향·R03 없는 EQP_HOLD는 0건이며 gate 비활성에도 base Service는 독립 실행된다 | FR-A-05, FR-C-03 | V4-A-4.5, V4-C-5.3 | 1.5h |
| V4-C-5.5 | P1 | incident model gate fixture. 완료: DISABLED/UNAVAILABLE·coverage 미달·mixed model/score/threshold version·policy 불일치·score/threshold NULL은 base 유지, `incident_score == action_threshold` 경계 상향, 기존 WARNING/EQP_HOLD 비하향, R03 없는 EQP_HOLD 금지와 decorator on/off 동등성 test를 통과한다 | FR-A-05, FR-C-03, FR-C-14 | V4-C-5.4 | 1.0h |

### V4-C-6. HITL·상태 전이

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-6.1 | P1 | EQP_HOLD 요청 transaction. 완료: action/approval/run/audit를 먼저 원자 커밋하고 승인요청 EMAIL 1회 후 interrupt, 승인 전 MES 0회 | FR-C-04, FR-C-05, FR-C-12 | V4-C-0.4, V4-C-5.3, V4-C-7.2 | 1.5h |
| V4-C-6.2 | P1 | 조건부 승인·반려. 완료: PENDING 1회만 결정, 중복·expired 409, 행 재생성 0건 | FR-C-05 | V4-C-6.1 | 2.0h |
| V4-C-6.3 | P1 | graph 재개. 완료: 승인→MES node, 반려→미전송 완료, 같은 thread·action ID 유지 | FR-C-04, FR-C-05 | V4-C-6.2 | 1.5h |
| V4-C-6.4 | P1 | checkpoint 유실 복구. 완료: DB 상태에서 State·Tool count·action/approval 복원, 중복 효과 0건 | FR-C-04, FR-C-08 | V4-C-6.3, V4-C-2.2 | 1.0h |

### V4-C-7. 이메일·MES Mock·API

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-7.1 | P1 | Agent/action/approval API. 완료: `POST /agent/runs`는 `{alarm:{source,alarm_id}}`만 허용하고 source 없는 `{alarm_id}`는 422로 거부한다. 요구사항 11.1의 나머지 C 업무 API와 `POST /agent/runs/{run_id}/retry`, n8n write-back `POST /internal/actions/{action_id}/delivery`, 운영자용 `POST /actions/{action_id}/deliveries/{channel}/retry`, AlarmRef·channel 상태와 404/409/422/503 계약을 검증한다 | FR-C-01, FR-C-04, FR-C-05, FR-C-06, FR-C-13 | V4-C-3.4, V4-C-5.3, V4-C-6.2 | 1.5h |
| V4-C-7.2 | P1 | n8n 이메일 adapter. 완료: WARNING email과 action/approval commit 이후 EQP_HOLD 승인요청 email, workflow write-back·timeout·실패 기록 | FR-C-06, FR-C-12 | V4-C-5.3, V4-CM-2.4 | 1.5h |
| V4-C-7.3 | P1 | MES Mock adapter. 완료: 승인된 EQP_HOLD만 호출, 비활성 설정, 실제 MES credential 0건 | FR-C-06, FR-C-12 | V4-C-6.3 | 1.5h |
| V4-C-7.4 | P1 | `send_action` Tool 계약·channel 멱등성. 완료: 입력은 `action_id`만 허용하고 run/channel은 저장 상태에서 파생한다. `{ok,action_id,reason}` 골격과 `deliveries:[{channel,status,sent,duplicate}]`를 검증하고 단일 top-level `sent`는 거부한다. 성공·실패 reason 접두어, `(action_id,channel)`별 효과 최대 1회와 hash 충돌 fixture를 통과하며 응답 유실은 `UNKNOWN`으로 기록하고 자동 retry는 0건이다 | FR-C-06, FR-C-12 | V4-C-7.2, V4-C-7.3, V4-CM-2.4 | 1.5h |

### V4-C-8. 배치·복구·E2E

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-8.1 | P1 | one-shot pending·FAILED rerun command. 완료: 자동 재실행 신규 run 0건. FAILED 수동 재실행은 원 run을 변경하지 않고 `retry_of_run_id`를 가진 새 run을 생성한다. action 생성 전 실패면 새 action을 허용하고, 생성 후 실패면 기존 action을 `REUSED` link로 연결해 action·approval·delivery 추가 0건 | FR-C-09 | V4-C-1.3, V4-C-5.3 | 2.0h |
| V4-C-8.2 | P0 | Runtime reset·fixture. 완료: `kosa_agent_e2e`만 allowlist로 허용해 실행 데이터를 초기화하고 `kosa_agent`·`kosa_text2sql` 대상은 거부하며 corrected source·schema를 보존한다 | 요구사항 13장 | V4-CM-3.4, V4-C-0.2 | 2.0h |
| V4-C-8.3 | P1 | 상태 복구 runner. 완료: stale run·`SENDING`을 delivery 존재/부재/hash 충돌로 분기하고 결과를 확인할 수 없으면 `UNKNOWN`으로 전이해 자동 재발송하지 않는다. 운영자 reconciliation 뒤에만 `FAILED` 또는 `SENT`로 정리하며 중복 email/MES 0건이다 | FR-C-06, FR-C-09 | V4-C-7.4, V4-C-8.2 | 1.5h |

### V4-C-9. Agent React

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-9.1 | P1 | 실행·근거 화면. 완료: predicted 표기, confidence·limitations, 실제 alarm/relation/chunk deep link | FR-C-13 | V4-C-7.1, V4-B-8.1 | 2.0h |
| V4-C-9.2 | P1 | 조치·승인 화면. 완료: 3단계 action, 승인·반려, email/MES 채널별 상태와 409 처리 | FR-C-13 | V4-C-7.1, V4-C-7.4 | 1.5h |
| V4-C-9.3 | P1 | polling·복원. 완료: 2초 polling·terminal stop·timer cleanup·direct URL·4상태·Mock 0건 | FR-C-13, NFR-17 | V4-C-9.1, V4-C-9.2 | 1.5h |

### V4-C-10. 최종 Agent 평가

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-C-10.1 | P1 | E2E 시나리오. 완료: base MONITORING, VERIFIED `score >= action_threshold` SUMMARY-only WARNING 상향, NULL/UNVERIFIED 기본 유지, WARNING email, EQP 승인요청 EMAIL→HITL→승인 후 MES, 반려, Tool/email/MES 실패와 중복 0건, synthetic label Runtime 조회 0건 | FR-C-03, FR-C-04, FR-C-05, FR-C-06, FR-C-08, FR-C-12, FR-C-14 | V4-C-5.5, V4-C-8.1, V4-C-8.3, V4-C-9.3 | 1.5h |
| V4-C-10.2 | P1 | Agent artifact. 완료: Level 1/2 완료율·Tool 수·latency·token·근거 점검·구조 성공률, base/gated action delta와 threshold revision을 기록한다. 실제 Fault supervised 성능은 없고 synthetic label 원문은 저장하지 않는다 | FR-C-01, FR-C-02, FR-C-15 | V4-C-4.2, V4-C-10.1 | 1.0h |

**C 합계: 62.0h**

## 7. D — Analytics

### V4-D-0. 신규 스키마 계약

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-0.1 | P0 | base·Runtime schema inventory. 완료: `001`·`002`의 profile별 table/column/enum/nullability와 legacy 대응표, 허용 목적 기록 | FR-D-01, FR-D-02 | V4-CM-1.7, V4-CM-2.4 | 1.5h |
| V4-D-0.2 | P0 | DTO·Tool 계약. 완료: query/validate/history/evaluation/audit와 metric/chart enum contract test | FR-D-01, FR-D-04~09 | V4-CM-0.2, V4-D-0.1 | 1.0h |

### V4-D-1. 공용 서버 pool·allowlist

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-1.1 | P0 | Runtime/evaluation 논리 DB preflight. 완료: 같은 source manifest, Runtime write state와 immutable 평가 snapshot 구분 | FR-D-03, FR-D-08 | V4-CM-1.7, V4-CM-2.7 | 1.5h |
| V4-D-1.2 | P0 | process별 pool factory. 완료: Runtime readonly/logger와 evaluation readonly/logger 분리, fallback·전체 DSN 로그 0건 | FR-D-03, FR-D-08 | V4-D-1.1, V4-CM-2.6 | 1.5h |
| V4-D-1.3 | P0 | pool별 schema cache. 완료: 논리 key로 분리, information_schema 1회 조회, migration 차이를 정상 처리 | FR-D-02, FR-D-03 | V4-D-1.2 | 1.0h |

### V4-D-2. sqlglot 안전 검증

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-2.1 | P0 | red/green fixture. 완료: 정상 JOIN/GROUP/CTE와 쓰기·다중문장·비허용 객체·위험 함수·catalog 공격 | FR-D-02 | V4-D-0.1 | 2.0h |
| V4-D-2.2 | P0 | AST·allowlist 검증기. 완료: CTE/서브쿼리 재귀 검사, db+name 정규화, 무스키마 catalog 차단 | FR-D-02 | V4-D-1.3, V4-D-2.1 | 2.0h |
| V4-D-2.3 | P0 | LIMIT·정규화·재검증. 완료: 최대 500행, 변환 SQL 전체 재파싱, normalized SQL·check 결과 | FR-D-02 | V4-D-2.2 | 1.5h |

### V4-D-3. Analysis Plan Tool

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-3.1 | P0 | 신규 schema context. 완료: parameter·alarm source·3단계 action·Runtime table 설명, 공개 Fault GT 필드 제외 | FR-D-01 | V4-D-1.3 | 1.5h |
| V4-D-3.2 | P1 | 구조화 plan 생성. 완료: SQL·metric·grouping·chart Pydantic, timeout·1회 교정·model metadata | FR-D-01 | V4-D-0.2, V4-D-3.1 | 1.5h |
| V4-D-3.3 | P1 | Tool 오류 계약. 완료: LLM_NOT_READY·POLICY_REJECTED·DEPENDENCY_ERROR 예외 미전파, Agent 예산 미포함 | FR-D-01 | V4-D-3.2 | 1.0h |

### V4-D-4. 질의 실행·로그

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-4.1 | P0 | readonly executor. 완료: statement timeout·row limit, writer pool에 생성 SQL 전달 0건 | FR-D-03 | V4-D-1.2, V4-D-2.3 | 1.5h |
| V4-D-4.2 | P0 | QueryLog 4상태. 완료: 성공·정책거부·검증실패·DB오류를 고정 INSERT, 로그 실패 시 query pool 재실행 0건 | FR-D-05 | V4-D-4.1 | 1.5h |
| V4-D-4.3 | P1 | pipeline. 완료: plan→validate→execute→metric/chart→response/log, 정책 위반 재생성·실행 0회 | FR-D-03~05 | V4-D-3.3, V4-D-4.2 | 1.5h |
| V4-D-4.4 | P1 | 제한적 교정. 완료: 읽기 구문·없는 컬럼만 1회, 두 번째도 전체 validator, Agent retry와 분리 | FR-D-02 | V4-D-4.3 | 1.0h |

### V4-D-5. Metric·chart·API

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-5.1 | P1 | metric 계산. 완료: scalar/group·ratio·percentile 경계, null·빈 결과 계약 | FR-D-04 | V4-D-4.3 | 1.5h |
| V4-D-5.2 | P1 | chart compatibility. 완료: line/bar/histogram/table 보정 규칙, Frontend 재판정 0건 | FR-D-04 | V4-D-5.1 | 1.5h |
| V4-D-5.3 | P1 | query·validate API. 완료: `POST /analytics/query`·`POST /analytics/validate`, 정책거부 200, malformed 422, dependency 503, execution 여부 명확 | FR-D-06, FR-D-09 | V4-D-4.4, V4-D-5.2 | 1.0h |

### V4-D-6. 이력·감사 API

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-6.1 | P1 | 질의·평가 이력 API. 완료: `GET /analytics/history`·`GET /analytics/evaluations`, 필터·stable pagination·파일 없음 empty·질문 재실행 | FR-D-05, FR-D-06, FR-D-08 | V4-D-4.2 | 1.5h |
| V4-D-6.2 | P0 | 감사 Repository. 완료: event/actor/entity/date 필터, append-only, Text2SQL로 audit 조회 0회 | FR-D-07 | V4-CM-2.4 | 1.5h |
| V4-D-6.3 | P1 | 감사 API. 완료: `GET /audit-logs`, stable pagination·before/after·channel delivery entity 추적·contract test | FR-D-07 | V4-D-6.2 | 1.5h |
| V4-D-6.4 | P1 | 권한 통합. 완료: readonly/logger/app 허용·거부 SQLSTATE와 비밀 출력 0건 | NFR-01 | V4-D-1.2, V4-D-6.3 | 1.0h |

### V4-D-7. 신규 Text2SQL 평가

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-7.1 | P1 | 신규 질문셋 작성. 완료: Fault 정답 질문 0건, `kosa_text2sql`의 실제 evaluation allowlist인 base/reference·R03·document·Mock action·`nl_query_log` 범위에서 12문항 이상과 기대 SQL·결과·정렬을 정의하며 runtime·audit table 질문은 포함하지 않는다 | FR-D-08 | V4-D-0.1, V4-D-1.1, V4-A-2.4, V4-B-1.4 | 2.0h |
| V4-D-7.2 | P1 | comparator·방어셋. 완료: exact/float tolerance/conditional order/chart x-y와 6종 방어 판정 | FR-D-08 | V4-D-2.3, V4-D-7.1 | 1.5h |
| V4-D-7.3 | P1 | evaluation runner. 완료: immutable 공용 DB preflight, 12문항 중 10건 이상 정답, 결과·attempt·model/prompt/source hash artifact 원자 저장 | FR-D-08 | V4-D-1.1, V4-D-7.2 | 1.0h |

### V4-D-8. Analytics React

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-8.1 | P1 | 자연어 분석 화면. 완료: 질문·SQL·표·metric·backend chart plan, timeout 120초 이상 | FR-D-06 | V4-D-5.3 | 1.5h |
| V4-D-8.2 | P1 | history·evaluation·audit 화면. 완료: 이력 필터·재실행·평가·before/after·direct URL | FR-D-06~08 | V4-D-6.1, V4-D-6.3, V4-D-7.3 | 1.0h |

### V4-D-9. Analytics 통합 검증

| ID | 우선 | 작업·완료 기준 | 근거 | 선행 | 시간 |
|---|---|---|---|---|---:|
| V4-D-9.1 | P1 | API·DB·React E2E. 완료: 정상·거부·검증실패·DB오류·empty를 실 pool과 화면에서 확인 | FR-D-01~09 | V4-D-7.3, V4-D-8.1, V4-D-8.2 | 1.5h |
| V4-D-9.2 | P1 | Analytics artifact. 완료: SQL 정확도·방어·metric/chart·source/schema/model revision과 미검증 사항 기록 | FR-D-08 | V4-D-9.1 | 1.0h |

**D 합계: 40.5h**

## 8. Gate와 실행 순서

| Gate | 완료 조건 | 해제되는 작업 |
|---|---|---|
| G0 신규 기준 | V4-CM-0·V4-CM-1 완료, corrected source와 계약 동결 | V4-A-0, V4-B-0, V4-D-0, V4-CM-2 |
| G1 Runtime | V4-CM-2 검증, AlarmRef·3단계 action·delivery 스키마 준비 | V4-C-0·V4-C-1, D pool, API Repository |
| G2 도메인 핵심 | A 규칙 알람·모델 비의존 Summary Tool, B corrected corpus/Tool, D validator, C 모델 비의존 base action 완료 | C 실제 evidence graph·각 API; A-4.5 이후 incident model gate는 병렬 2단계 통합 |
| G3 Tool·API | A/B/C/D contract test·OpenAPI 통과 | 각 React 실연동 |
| G4 상태·복구 | C HITL·delivery·reset·recovery·anomaly gate, D evaluation snapshot | 통합 E2E·평가 |
| G5 통합 | 역할별 artifact와 V4-CM-3 전체 통과 | 최종 시연·제출 |

핵심 경로는 `V4-CM-0 → V4-CM-1 → V4-CM-2 → V4-C-0/V4-C-1 → V4-C-5/V4-C-6/V4-C-7 → V4-C-8/V4-C-10 → V4-CM-3`이다. A·B·D는 G0 이후 병렬로 진행한다.

## 9. P2 — 채택 범위 제외

| ID | 담당 | 작업 | 근거 | 선행 |
|---|---|---|---|---|
| V4-A-X1 | A | 대체 비지도 모델과 동일 protocol 비교 | FR-A-09 | V4-A-7.2 |
| V4-B-X1 | B | vector+keyword hybrid retrieval 비교 | FR-B-08 | V4-B-8.2 |
| V4-C-X1 | C | Level 3 ReAct | FR-C-11 | V4-C-10.2 |
| V4-D-X1 | D | Analysis Tool MCP wrapping | FR-D-10 | V4-D-9.2 |
| V4-CM-X1 | 공동 | 전문가 라벨셋 수신 시 supervised 평가 트랙 추가 | FR-C-15 | label source·review 절차 확정 |

P2는 213.0h 합계에 포함하지 않는다.

## 10. 완료 보고 체크리스트

- source ZIP·corrected copy·DB의 revision과 hash를 기록했는가.
- 평가 artifact에 `ground_truth_available`와 `label_source`가 있는가.
- synthetic artifact에 `label_source=SYNTHETIC_GENERATOR`·`production_ground_truth_available=false`·
  `usage_scope=EVALUATION_ONLY`가 있고 Runtime·Agent·Text2SQL·RAG 유입이 0건인가.
- 공개 Fault 정답이 없는 상태에서 실제 공정 supervised 분류 성능을 계산하지 않았으며 synthetic
  지표를 generator agreement로만 표시했는가.
- anomaly gate가 `VERIFIED action_threshold`에서 Summary-only MONITORING을 WARNING으로만 상향하고,
  하향·R03 없는 EQP_HOLD·NULL/UNVERIFIED 상향을 모두 차단하는가.
- 제공 action Mock을 Runtime seed·expected action으로 사용하지 않았는가.
- 공용 DB write 전에 host·database guard와 팀 공유 절차를 통과했는가.
- Runtime reset이 source·reference·corpus를 보존하는가.
- 이메일과 MES Mock 효과가 `(action_id, channel)`마다 최대 1회인가.
- 모든 React 화면이 Loading·Error·Empty·Success를 구분하는가.
- 실행 테스트와 결과, 관련 FR, artifact, 미검증 항목을 남겼는가.

## 11. 문서 동기화 대상

- 요구사항정의서 v2.0
- 시스템설계서 v2.0
- 역할분담 v10.0
- API 명세 차기 버전
- `docs/ai-context/`와 역할별 Task 문서
- 신규 데이터 bootstrap·Runtime migration·테스트 fixture

본 WBS가 확정되기 전까지 기존 WBS의 구 데이터 고정 수치와 supervised Fault 평가 Task를 신규 구현 근거로 사용하지 않는다.
