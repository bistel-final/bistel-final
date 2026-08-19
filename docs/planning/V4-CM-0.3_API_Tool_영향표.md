# V4-CM-0.3 API·Tool 영향표

> [!NOTE]
> **상태 (2026-08-18)** — 본 영향표는 kosa_0813 전환 시점 기준입니다. 멘토 최종 패키지의
> API 확정(02 가이드)으로 외부 계약이 변경되었으므로, 현행 API·Tool 기준은
> `docs/ai-context/04-api-tool-contracts.md`를 따르십시오.


> 기준 요구사항: `요구사항정의서_v2_0_작업본.md`
> 기준 설계: `시스템설계서_v2_0_작업본.md`
> 데이터 epoch: 멘토 최종 패키지(2026-08-18) / 구: kosa_0813
> 상태: v2 공통 계약 동결을 위한 구→신 영향 분석. anomaly gate와 synthetic 평가 gold는
> 멘토 확인 전 `[팀 잠정]` 계약이다.

## 1. 판정 기준

| 판정 | 의미 |
|---|---|
| 유지 | 경로·책임·핵심 DTO가 v2에서도 같음 |
| 변경 | 기존 경로나 기능은 살리지만 입출력·의미·저장 소스가 바뀜 |
| 추가 | v2 흐름에 필요하지만 현재 계약·구현에 없음 |
| 폐기 | 구 데이터·구 조치·공개 Fault 정답에만 의존하여 v2에서 사용하지 않음 |
| adapter | React 하위 호환 또는 점진 전환에만 쓰고 canonical 저장·Service에서는 사용하지 않음 |

현재 Backend 도메인 Router는 모두 빈 골격이다. 따라서 이 문서의 “현재”는 주로
`schemas.py`, `app/common/tool_contracts.py`, React API wrapper·Mock을 뜻한다.

## 2. 공통 Enum·ID·DTO

| 현재 계약 | 판정 | v2 canonical 계약 | 소유/후속 Task | 영향 파일 |
|---|---|---|---|---|
| `Judgement=IN_CONTROL\|OOC\|OOS` | 변경 | `AlarmType=IN\|OOC\|OOS` | Common / V4-CM-0.2 | `common/enums.py`, A schemas·Mock |
| 단일 `alarm_id` | 폐기 | `AlarmRef {source: TRACE\|SUMMARY\|R03, alarm_id}` | Common·A·C / V4-CM-0.2, V4-A-5.1, V4-C-1.* | `common/schemas.py`, A/C DTO, React deep link |
| `MONITOR\|NOTIFY\|LOT_HOLD\|EQP_HOLD` | 폐기 | `MONITORING\|WARNING\|EQP_HOLD` | Common·C / V4-CM-0.2, V4-C-5.* | `common/enums.py`, Agent DTO·Mock |
| `SendChannel=EMAIL\|MES` | 변경 | `DeliveryChannel=EMAIL\|MES_MOCK` | Common·C / V4-CM-0.2, V4-C-7.* | Enum, action/delivery DTO |
| `SendStatus` 단일 상태 | 변경 | 채널별 `DeliveryStatus=BLOCKED\|WAITING\|SENDING\|SENT\|FAILED\|CANCELED\|UNKNOWN` | Common·C / V4-CM-0.2, V4-C-7.4, V4-C-8.3 | Enum, Agent DTO, n8n write-back |
| `FaultCode` | 변경 | `FaultHypothesis=FOC\|RFM\|MFD\|TMD\|OTH`; 정답이 아닌 `predicted_*` | Common·C / V4-CM-0.2, V4-C-3.* | Enum, Agent DTO·UI |
| `AgentRunStatus` | 변경 | `RunStatus=RUNNING\|WAITING_APPROVAL\|COMPLETED\|FAILED` | Common·C / V4-CM-0.2, V4-C-7.* | Enum, Agent DTO·UI |
| `ApprovalStatus`·`ActionApprovalStatus` 분리 | 변경 | canonical `ApprovalStatus=AUTO\|PENDING\|APPROVED\|REJECTED\|EXPIRED` 하나로 통일 | Common·C / V4-CM-0.2, V4-C-6.* | Enum, approval/action DTO |
| `Severity`, `Decision`, `ToolCallStatus`, `ChartType`, `ActorType` | 유지 | 값 공간과 책임 유지 | Common | Enum·공통 DTO |
| `SensorSummaryItem` | 변경 | parameter Summary·evaluation, `parameter_id`, `alarm_type` | Common·A / V4-CM-0.2, V4-A-4.* | Tool DTO, Detection DTO |
| scalar anomaly score 필수 | 변경 | WAFER 조회는 nullable structured `AnomalySignal(score, model_version, score_method, display_threshold?, is_anomaly?, action_threshold?, threshold_version?, threshold_validation_status)`, 조치 입력은 versioned `IncidentModelSignal`로 분리한다. Runtime DTO에 synthetic label field는 두지 않음 | Common·A·C / V4-CM-0.2, V4-A-3.*, V4-A-4.5 | Tool DTO, Summary API, incident batch Service, Agent policy input |
| 공개 Fault GT/Generator 내부값 | 변경 | 공개 실제 GT는 없음. corrected generator가 만든 별도 artifact만 `ground_truth_available=true`, `label_source=SYNTHETIC_GENERATOR`, `production_ground_truth_available=false`, `usage_scope=EVALUATION_ONLY`로 허용 | Common·A / V4-CM-0.4, V4-A-3.5~3.6 | evaluation artifact·manifest; Runtime/API/Tool DTO에는 없음 |
| `IncidentRef(lot_id,chamber_id)` | 유지 | `[팀 잠정]` incident key | C / V4-C-1.* | `common/schemas.py`, Agent DTO |
| prefix ID 생성기 | 유지 | Runtime ID 생성·`NonEmptyId`; R03/relation ID는 결정론 별도 계약 | Common·A·B | `common/ids.py`, A/B loader |

## 3. API 영향

### 3.1 A Detection

| endpoint | 판정 | v2 핵심 변경 | 소유/Task |
|---|---|---|---|
| `GET /dataset/bounds` | 추가 | epoch·min/max date·area/equipment/chamber/parameter 선택지 | A / V4-A-5.3 |
| `GET /dashboard/summary` | 변경 | `date_from`, `date_to`, `area` 명시; parameter·chamber 서버 집계 | A / V4-A-5.3 |
| `GET /parameters` | 추가 | 독립 endpoint가 없던 구 sensor catalog를 8 parameter·5선·`upper_only` 계약으로 제공 | A / V4-A-5.2 |
| `GET /alarms` | 변경 | `date_from/date_to/area` 필수, TRACE·SUMMARY 기본, R03 명시 포함 | A / V4-A-5.1 |
| `GET /alarms/{alarm_id}` | 폐기 | `GET /alarms/{source}/{alarm_id}`; ID 패턴으로 source 추정 금지 | A / V4-A-5.1 |
| `GET /summaries/{lot_hist_id}` | 폐기 | 공개 REST에서 제거. Agent용 Summary는 `get_fdc_summary(lot_hist_id)` Tool로만 제공 | A / V4-A-4.*, V4-C-2.* |
| `GET /trace` | 추가 | 신규 가이드 단일 lot·wafer·chamber·parameter 계약 | A / V4-A-5.2 |
| `GET /traces/catalog` | 변경 | parameter 용어·corrected seq 0~5, anomaly nullable | A / V4-A-5.2 |
| `POST /traces/search` | 변경 | `parameter_ids`, step 경계, corrected Trace | A / V4-A-5.2 |

### 3.2 B Knowledge

| endpoint | 판정 | v2 핵심 변경 | 소유/Task |
|---|---|---|---|
| `GET /relations/chambers/{chamber_id}` | 변경 | 고정 설비 upstream 제거; ProcessStep adjacency·stable `relation_id`·`graph_revision` | B / V4-B-5.1 |
| `GET /relations/equipment/{equipment_id}` | 변경 | 동일. LOT routing 반환 금지 | B / V4-B-5.1 |
| `POST /documents/search` | 변경 | 모든 hit에 `corpus_revision`, stable document/chunk ID | B / V4-B-5.2 |
| `GET /documents/{document_id}` | 변경 | ACTIVE corpus revision과 정정 문서만 반환 | B / V4-B-5.2 |

### 3.3 C Agent·HITL·delivery

| endpoint | 판정 | v2 핵심 변경 | 소유/Task |
|---|---|---|---|
| `POST /agent/runs` | 변경 | body는 `{alarm:{source,alarm_id}}`; 단일 `{alarm_id}` 금지·422 | C / V4-C-7.1 |
| `GET /agent/runs`, `GET /agent/runs/{run_id}` | 변경 | `predicted_*`, provenance, AlarmRef 목록, 채널별 delivery | C / V4-C-7.1 |
| `POST /agent/runs/{run_id}/retry` | 추가 | FAILED만 새 run·`retry_of_run_id`; 기존 action 재사용 규칙 | C / V4-C-7.1, V4-C-8.1 |
| `GET /actions`, `GET /actions/{action_id}` | 변경 | 3단계 action·EMAIL/MES_MOCK 복수 delivery | C / V4-C-7.1 |
| `GET /approvals`, `POST /approvals/{approval_id}/decision` | 변경 | EQP_HOLD만 HITL; approval EMAIL은 이전에 전송, 승인 후 MES_MOCK | C / V4-C-6.*, V4-C-7.1 |
| `POST /internal/actions/{action_id}/delivery` | 추가 | 신규 n8n channel write-back, provider ID·hash·`UNKNOWN` | C / V4-C-7.1 |
| `POST /actions/{action_id}/deliveries/{channel}/retry` | 추가 | FAILED만 운영자 명시 재시도; UNKNOWN 자동 재발송 금지 | C / V4-C-7.1, V4-C-8.3 |

### 3.4 D Analytics

| endpoint | 판정 | v2 핵심 변경 | 소유/Task |
|---|---|---|---|
| `POST /analytics/query` | 변경 | 신규 9-base/reference allowlist, 정책 거부 200, Fault 정답 질문 금지 | D / V4-D-5.3 |
| `POST /analytics/validate` | 유지 | 모든 실행 경로에 동일 validator | D / V4-D-5.3 |
| `GET /analytics/history` | 변경 | runtime/evaluation log pool 분리, base hash에서 log 누적 제외 | D / V4-D-6.1 |
| `GET /analytics/evaluations` | 변경 | `kosa_text2sql` 실제 schema 범위만; Fault·Runtime/audit 질문 제외 | D / V4-D-6.1, V4-D-7.* |
| `GET /audit-logs` | 유지 | Runtime `kosa_agent`의 append-only audit 조회; evaluation snapshot 질문과 분리 | D / V4-D-6.3 |

`/health`·`/health/ready`는 유지하되 업무 API 개수에서 제외한다. ready는
PostgreSQL·Neo4j·n8n을 병렬 timeout으로 검사하고 Neo4j success marker·ACTIVE corpus를 확인한다.

## 4. Tool 5종 영향

| Tool | 판정 | v2 입력 | v2 출력 | 소유/Task |
|---|---|---|---|---|
| `get_fdc_summary` | 변경 | `lot_hist_id` | parameter Summary·limit·evaluation과 nullable structured `AnomalySignal(score, model_version, score_method, display_threshold?, is_anomaly?, action_threshold?, threshold_version?, threshold_validation_status)`. 모델·threshold 미준비 시 summary는 정상 반환하고 signal은 null이며 synthetic label 원문은 반환 금지 | A / V4-A-4.1~4.4 |
| `get_equipment_context` | 변경 | `chamber_id` | 정적 설비·AREA·ProcessStep adjacency, `relation_id`, `graph_revision`; LOT routing 없음 | B / V4-B-2.3, V4-B-4.1 |
| `search_documents` | 변경 | query·model_code?·top_k | `corpus_revision`이 있는 DocumentHit | B / V4-B-3.3, V4-B-4.2 |
| `send_action` | 변경 | `action_id`만 | `{ok,action_id,reason}` + 실행 가능한 `deliveries:[{channel,status,sent,duplicate}]`; 단일 top-level `sent` 폐기 | C / V4-C-7.4 |
| `generate_analysis_plan` | 유지 | question | SQL·metric·visualization; Agent Tool 예산 밖 | D / V4-D-5.1 |

`send_action` 입력의 구 `agent_run_id`·channel은 폐기한다. Service가 action·approval·delivery
저장 상태에서 run과 실행 channel을 파생한다.

멘토 원안의 단일 `sent`는 EMAIL·MES Mock의 부분 성공·중복·`UNKNOWN`을 표현할 수 없어
채널별 `deliveries`로 의도적으로 확장한다. 공통 `{ok, action_id, reason}` 골격은 유지한다.

WAFER별 `get_fdc_summary`의 `AnomalySignal`은 C의 조치 함수에 직접 전달하지 않는다. A의 내부
`build_incident_model_signal(lot_hist_ids, action_policy_version)`가 중복 제거·안정 정렬한 incident
member를 단일 batch로 조회해 다음 조건을 만족하는 `IncidentModelSignal`을 만든다.

```text
enabled == true
status == READY
expected_member_count == valid_member_count > 0
all members have non-null score and VERIFIED threshold
model_version, score_method, display_threshold, action_threshold, threshold_version are homogeneous
action_policy_version is allowlisted
incident_score = max(member scores)
incident_score >= action_threshold
```

조건이 하나라도 거짓이면 `DISABLED` 또는 `UNAVAILABLE`로 반환하고 base rule을 사용한다. 조건이 참이어도 Summary-only base
`MONITORING`을 `WARNING`으로 한 단계 상향하는 것만 허용한다. 기존 `WARNING`·`EQP_HOLD`를
하향하거나 R03 없이 `EQP_HOLD`를 생성할 수 없다. synthetic label은 threshold를 검증하는 평가
artifact일 뿐 Tool·Runtime·Agent 입력이 아니다.

C의 `V4-C-5.1~5.3` base policy·fixture·action Service는 A 모델과 `V4-A-3.6`을 기다리지 않고
완료한다. anomaly 신호는 `V4-C-5.4` policy decorator에서 2단계로 주입하고, gate 전용 경계·
fail-closed 검증은 `V4-C-5.5`가 담당한다. 따라서 모델·threshold 장애나 미준비 상태가 Agent 기본
조치 흐름을 차단하지 않는다.

A의 `V4-A-4.1~4.3` Summary Service·Tool·C wrapper 계약도 모델 검증을 기다리지 않는다.
미준비 상태에서는 규칙 summary와 `AnomalySignal=null`로 정상 동작하고, `V4-A-4.4`가
`V4-A-3.6` 이후 검증된 WAFER signal population을 연결한다. `V4-A-4.5`가 이를 versioned
`IncidentModelSignal`로 batch 집계한 이후에만 C gate를 활성화한다.

## 5. 기존 `schemas.py` DTO 영향

Page wrapper처럼 구조를 그대로 쓰는 경우도 내부 item이 바뀌면 `변경`으로 분류한다.
아래 표에 없는 신규 DTO는 해당 도메인 Task에서 추가하되 공통 payload를 재정의하지 않는다.

| 현재 DTO | 판정 | v2 처리 | 소유/Task |
|---|---|---|---|
| `AlarmItem`, `AlarmPageResponse` | 변경 | `AlarmRef`를 가진 `UnifiedAlarmItem`; source별 값·limit·nullable action을 반환 | A / V4-A-5.1 |
| `HierarchyNode`, `DailyTrendItem`, `ChamberAlarmCount`, `EquipmentCountItem` | 변경 | 동일 필터의 dashboard 집계용으로 유지하되 신규 parameter·source 기준으로 계산 | A / V4-A-5.3 |
| `TopSensorItem` | 폐기 | `TopParameterItem`으로 교체 | A / V4-A-5.3 |
| `DashboardSummaryResponse` | 변경 | 필수 기간·AREA 적용값, source별 건수·line trend·parameter/chamber 비교를 반환 | A / V4-A-5.3 |
| `FdcSummaryResponse` | 폐기 | 공개 `/summaries` 중복 DTO 제거; common `FdcSummaryToolResult`를 Agent Tool 계약으로 단일화 | A / V4-A-4.1~4.4 |
| `SensorLimits`, `TraceCatalogSensor` | 폐기 | `ParameterLimits`, `TraceCatalogParameter`로 교체 | A / V4-A-5.2 |
| `TraceCatalogArea`, `TraceCatalogEquipment`, `TraceCatalogRecipe`, `TraceCatalogLot` | 변경 | 8/13 corrected 선택지·ID로 재생성 | A / V4-A-5.2 |
| `TraceCatalogAnomaly` | adapter | React 다중 Trace의 optional 표시 정보. canonical 조치 입력은 이 adapter가 아니라 `get_fdc_summary`의 검증 provenance를 가진 anomaly signal만 사용 | A / V4-A-5.2 |
| `TraceCatalogResponse` | 변경 | parameter catalog·dataset revision 포함 | A / V4-A-5.2 |
| `TraceSearchRequest` | 변경 | `sensor_ids`→`parameter_ids`; step 1·2와 corrected seq 0~5 경계 | A / V4-A-5.2 |
| `TracePoint`, `TraceWaferSeries`, `MeasuredStepStat`, `TraceSearchResponse` | 변경 | parameter·`alarm_type`·corrected seq 기준으로 교체 | A / V4-A-5.2 |
| `ChamberRelationResponse`, `EquipmentRelationResponse` | 변경 | static adjacency·stable `relation_id`·`graph_revision`; fixed upstream·LOT route 제거 | B / V4-B-5.1 |
| `DocumentSearchRequest`, `DocumentSearchResponse` | 변경 | ACTIVE corpus hit와 `corpus_revision` 반환 | B / V4-B-5.2 |
| `DocumentChunkItem`, `DocumentDetailResponse` | 변경 | stable IDs·ACTIVE corpus revision·정정 문서만 반환 | B / V4-B-5.2 |
| `AgentRunCreateRequest`, `AgentRunAcceptedResponse` | 변경 | 단일 `alarm_id`→요청·대표 `AlarmRef` | C / V4-C-7.1 |
| `IncidentAlarmEvidence`, `R03EvidenceRef` | 변경 | source-aware AlarmRef·parameter·`[팀 잠정]` R03 근거 | C / V4-C-2.*, V4-C-3.* |
| `BatchIncidentPlan` | 폐기 | 구 cross-incident action plan을 제거하고 조치는 incident별 순수 정책 함수 결과만 기록 | C / V4-C-3.* |
| `UpstreamEvidence` | 폐기 | 고정 upstream 모델을 제거하고 AlarmRef별 PostgreSQL route·graph consistency 근거로 대체 | C / V4-C-1.4, V4-C-2.4 |
| `AgentEvidence` | 변경 | `lot_hist_id`·`relation_id`·`graph_revision`·`chunk_id`·`corpus_revision` provenance와 `route_consistency` 기록. model gate를 사용하면 `IncidentModelSignal`의 status·coverage·incident score·max member·model/score/threshold/policy provenance를 snapshot | C / V4-C-2.4, V4-C-5.* |
| `DeliveryResult` | 폐기 | `DeliveryItem` 목록으로 교체하고 `(action_id, channel)`별 상태를 표현 | C / V4-C-5.*, V4-C-7.1 |
| `ToolCallItem` | 유지 | 공통 Tool payload 변경만 반영; call sequence·status·latency 계약 유지 | C / V4-C-7.1 |
| `ApprovalItem`, `ApprovalDecisionRequest`, `ApprovalDecisionResponse`, `ApprovalPageResponse` | 변경 | EQP_HOLD만 승인; EMAIL 이후 PENDING, 승인 후 MES_MOCK 규칙 | C / V4-C-6.*, V4-C-7.1 |
| `AgentRunItem`, `AgentRunDetailResponse`, `AgentRunPageResponse` | 변경 | `predicted_*`와 nullable `reviewed_*` 분리, AlarmRef·provenance·delivery 목록 | C / V4-C-3.*, V4-C-7.1 |
| `ActionItem`, `ActionDetailResponse`, `ActionPageResponse` | 변경 | 3단계 action·복수 channel delivery·created/reused run link | C / V4-C-5.*, V4-C-7.1 |
| `AnalysisQueryRequest`, `AnalysisQueryResponse`, `GroupedMetricResult` | 변경 | 신규 schema allowlist·정책 거부 200 구조를 유지하고 구 Fault 정답 질문을 거부 | D / V4-D-5.* |
| `NlQueryLogItem`, `NlQueryHistoryResponse` | 변경 | runtime/evaluation pool과 dataset·schema revision을 추적 | D / V4-D-6.1 |
| `SqlValidateRequest`, `ValidationCheck`, `SqlValidateResponse` | 유지 | 신규 allowlist를 주입하되 DTO·안전 실패 구조 유지 | D / V4-D-2.*, V4-D-5.3 |
| `EvaluationItem`, `EvaluationResponse`, `EvaluationListResponse` | 변경 | evaluation 실제 schema·versioned regression fixture만 평가 | D / V4-D-7.* |
| `AuditLogItem`, `AuditLogResponse` | 유지 | Runtime append-only audit 조회에만 사용 | D / V4-D-6.3 |

공통 `ApiModel`, `PageResponse`, `IncidentRef`, health/readiness DTO는 유지한다.
`AlarmRef`와 source-qualified token helper는 추가한다.

## 6. React adapter·Mock 영향

| 현재 | v2 처리 | 소유/Task |
|---|---|---|
| `sensor_id`, `judgement`, 2026-06 Mock | parameter/alarm_type·2026-08 corrected fixture로 교체. canonical wrapper에서 구 필드 생성 금지 | A / V4-A-6.* |
| `/alarms/:alarmId` | route param은 `SOURCE:alarm_id`; API는 source/id로 분리 호출 | A / V4-A-6.2 |
| 대시보드 무필터 초기 호출 | `/dataset/bounds` 후 date range·`area=ALL` 명시 | A / V4-A-6.1 |
| 고정 설비 upstream Mock | ProcessStep 관계·PG route 분리, `relation_id`·revision 표시 | B / V4-B-6.* |
| 구 action·단일 send 상태 | 3단계 action·EMAIL/MES_MOCK 채널별 표시 | C / V4-C-9.* |
| anomaly score 단독 조치 표시 | base action과 gated action, `display_threshold`·`action_threshold`·검증 상태·policy reason을 구분. synthetic label은 화면 payload에 포함하지 않음 | A·C / V4-A-6.*, V4-C-9.* |
| `fault_code` 표시 | “예측 원인” `predicted_fault_code`; 검토 라벨과 별도 | C / V4-C-9.* |
| evaluation에 Runtime/audit 질문 | evaluation 실제 allowlist 범위로 축소 | D / V4-D-7.* |

기존 React 8개 Page 컴포넌트는 유지한다. 신규 가이드의 5개 기능 영역을 Page에 매핑하고,
상세 URL·legacy redirect는 별도 route pattern으로 다룬다.

## 7. DB·Repository 영향

| 구 가정 | v2 canonical | 후속 Task |
|---|---|---|
| 단일 `fdc_alarm` FK | source-aware `AlarmRef`; `v_alarm_event` 조회 + source Repository resolve | V4-CM-2.1, V4-A-5.1, V4-C-1.1 |
| 구 `sensor` master·summary | `dim_parameter`, `summary_data`, `evaluation` | V4-CM-1.*, V4-A-1.* |
| action 48 runtime seed | Runtime action 0; evaluation DB에만 `fixture_type=MOCK` profile metadata | V4-CM-2.2~2.4 |
| 단일 `send_status/send_channel` | `action_delivery(action_id, channel)` | V4-CM-2.4, V4-C-5.* |
| 구 runtime ALTER/backfill | profile 공통 `001_reference_extensions.sql` + runtime 전용 `002_agent_runtime_clean.sql` CREATE | V4-CM-2.* |
| Runtime·evaluation 단일 hash | source/corrected file manifest·runtime/evaluation DB profile manifest 분리 | V4-CM-1.1, V4-CM-2.7 |
| Generator 주입값을 DB/Agent 정답으로 적재 | 원본 Generator 불변, corrected generator에서 synthetic evaluation artifact만 별도 생성. 세 PostgreSQL DB·Neo4j·RAG·Runtime·Text2SQL allowlist에는 미적재 | V4-CM-1.2, V4-A-3.5~3.6, V4-CM-3.5 |

## 8. 적용 순서·완료 검증

1. `V4-CM-0.2` 공통 Enum·AlarmRef·Tool input/output, `ThresholdValidationStatus`, structured nullable `AnomalySignal`, versioned `IncidentModelSignal`을 계약 테스트로 고정한다. synthetic `label_source`는 Runtime DTO가 아니라 `V4-CM-0.4` 평가 메타데이터에서 관리한다.
2. A는 모델 없이 `V4-A-4.1~4.3` Summary Tool을 먼저 구현하고 `V4-A-4.4`에서 검증된 WAFER signal, `V4-A-4.5`에서 incident batch signal을 연결한다. C도 `V4-C-5.1~5.3` base action을 먼저 구현하고 A-4.5 완료 후 `V4-C-5.4~5.5` gate를 decorator로 연결한다.
3. A·B·C·D가 본 영향표의 나머지 소유 파일을 각자 schemas→Service→Repository→Router 순으로 구현한다.
4. React adapter·Mock은 Backend canonical DTO와 같은 fixture로 contract smoke를 통과한다.
5. API 명세서를 v2 endpoint·DTO로 재생성하고 OpenAPI와 대조한다.
6. 구 필드·Enum·endpoint 사용은 폐기 목록 또는 명시적 adapter 밖에 0건이어야 한다.

### V4-CM-0.3 산출물 완료 체크

- [x] 기존 DTO·endpoint의 유지·변경·추가·폐기·adapter 분류
- [x] endpoint·DTO·Tool별 소유자와 V4 Task 연결
- [x] 공통 Enum·AlarmRef·Tool contract test 통과

### 전체 v2 전환 종료 체크 — 후속 Task

- [ ] `sensor_id`·`judgement`·4단계 action·단일 `fdc_alarm` 신규 구현 0건
- [ ] `send_action(action_id)` 외 입력 0건
- [ ] `get_fdc_summary`가 nullable structured `AnomalySignal`을 사용하고, 조치 gate는 batch `IncidentModelSignal`만 사용하며 member별 Tool 호출 0건
- [ ] A 모델 미준비 상태에서도 Summary Tool이 규칙 데이터+null signal로 정상 동작하고, C base policy·action Service가 독립 실행되며 gate on/off가 decorator로 분리됨
- [ ] VERIFIED `action_threshold`가 아닌 상향, base action 하향, R03 없는 EQP_HOLD 0건
- [ ] `SYNTHETIC_GENERATOR` label의 Runtime·Agent·Text2SQL·Neo4j·RAG·API payload 유입 0건
- [ ] API 명세·OpenAPI·React wrapper 계약 일치
- [ ] 실제 GT 없음과 synthetic 평가를 분리하고 `production_ground_truth_available=false` 메타데이터 유지
