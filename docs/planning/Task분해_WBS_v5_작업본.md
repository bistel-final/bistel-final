# Task 분해 · WBS v5 (작업본)

> 기준 원천: 멘토님 제공 최종 `project.zip`(2026-08-18) · epoch `fdc_final_20260818`
> 기준 문서: 요구사항정의서 v2.1 · 시스템설계서 v2.1 · 역할분담 v10.1 · API명세서 v3
> 데이터 기준표: `docs/reference/mentor-final-20260818/README.md`
> 작성일: 2026-08-19

## 1. v5의 전제

v4까지의 `kosa_0813` epoch Task는 이력으로만 남긴다. 이 문서의 `V5-*` ID만 신규 구현 근거다.

### 1.1 확정된 전환 방침

| 항목 | 결정 | 근거 |
|---|---|---|
| DB 이름 | `kosa_agent` · `kosa_agent_e2e` · `kosa_text2sql` **유지** | 요구사항 §7.3, 설계 §2.4 |
| 전환 방식 | **fresh 재구축.** 옛 epoch 스키마를 비우고 최종 base DDL로 새로 만든다 | 최종 CSV가 현재 스키마에 적재 불가(§1.2) |
| DDL 소유 | 멘토님 `sample/schema/03_schema_clean.sql`은 **base 9 table 전용 정본**으로 원문 그대로 사용한다. RAG·Reference·Runtime extension DDL은 팀이 별도 migration으로 소유한다 | SHA `4a437efc…` pin · 설계 §3 |
| 실행기 | 적재·검증·guard는 **우리 runner**가 소유 | 멘토 `00_load.sh`는 단일 DB·initdb 훅·재실행 불가·guard 없음 |
| 로컬 reference smoke | 멘토님 `docker-compose.yml`·`deploy/postgres-init/00_load.sh`는 **격리된 일회성 reference smoke에만** 사용할 수 있다 | 공용 DB·3 profile bootstrap·수용 검증의 실행 근거로 사용 금지 |
| Runtime schema | **팀 소유.** 최종 DDL은 base 9 table만 만든다 | 설계 §3.4, 역할 §5.1 |
| 임베딩 | 벡터 검색 채택. 교육생 배포패키지 ①의 `BAAI/bge-m3`·1024차원을 고정하고 process당 1회 재사용한다 | 설계 §5.3, 역할 §7.1 |
| n8n | 실행 시작은 `POST /agent/runs`가 소유하며, delivery·write-back workflow 3종은 **팀 산출물**로 학원 공용 n8n에 import·연결한다. 팀 compose에는 n8n 서비스를 만들지 않는다 | 설계 §7.3, 역할 §8.1 |
| Kafka | **필수 범위. C 담당.** 팀 compose의 Kafka·MES Mock으로 구현한다 | 설계 §7.4, 역할 §8 |
| Text2SQL | **선택 확장.** 필수 인수 기준·5화면에서 제외 | 요구사항 §5.4 |

### 1.2 fresh 재구축이 필요한 이유

```text
현재 공용 DB   trace_alarm_history.wafer   smallint
최종 CSV       trace_alarm_history.wafer   'LOT004W002'   ← wafer_id 문자열
```

최종본은 `wafer`에 wafer_id 문자열을 넣으므로 현재 컬럼 타입으로는 적재 자체가 실패한다.
View 수정으로 해결되지 않고 base table 타입 변경이 필요하다. 현재 공용 DB에는 bootstrap
데이터만 있고 팀 업무 데이터가 0건이므로 재구축 손실이 없다.

### 1.3 살리는 자산

데이터와 수치는 교체하지만 v4에서 확립한 **안전 계약은 그대로 재사용**한다.

```text
mutation_runtime   advisory lock · target guard · transaction preamble · 모드 배타
schema_lock        공용 advisory key
artifact 계약       receipt STARTED→COMMITTED|ABORTED · marker-last · --rehearse · --preflight
manifest_v3        envelope · stage 계약 · exact key 검증
value_normalization  db-value-v1 정규화
verify_bootstrap_state  profile·stage·mismatch 수집·sanitized 실패 출력
Runtime table 설계  9종 + action/severity pair CHECK (설계 §3.4 확정본)
```

## 2. 공수 요약

| 영역 | 담당 | 공수 | 핵심 산출물 |
|---|---|---:|---|
| Common | 4명 공동, 통합 관리 방대혁 | 65.5h | 최종 intake·epoch·fresh bootstrap·safe graph·Runtime schema·계약·통합 gate·배포 |
| A Detection | 신동원 | 28.0h | 재계산·알람·R03·score·격리 평가·화면 1·2 |
| B Knowledge | 강연권 | 21.5h | Neo4j 44/85·RAG 정정·임베딩 검색·화면 4·5·후속 운영 검증 |
| C Agent/HITL | 방대혁 | 69.5h | LangGraph·조치·승인·n8n·Kafka·delivery·화면 3 |
| D Audit·확장 | 천승현 | 14.5h | 감사 read model·화면 3 감사 tab·선택 Text2SQL |
| **합계** | | **199.0h** | P2 도전 과제 제외 |

우선순위별 공수는 **P0 151.0h / P1 48.0h**이며 P2 3.5h는 합계에서 제외한다.
Task 수는 96건(P2 2건 포함)이다. 대부분의 Task는 1.0~2.0h이며 예외가 열아홉 개다.

- `V5-CM-1.6` **3.0h** — legacy cleanup. 구 corrected 구현 3,875줄 삭제와 verifier·Agent
  Runtime 대체 구현을 원자적으로 수행해야 소비자가 끊어진 중간 상태가 남지 않는다.
- `V5-CM-1.8` **4.0h** — manifest 재기준화. epoch 상수 전환만으로 기존 fixture 92건이
  깨지고(`test_reference_extensions_v5` 53 · `test_manifest_v3` 37 · `test_dataset_epoch` 2),
  여기에 profile manifest 발급 script 신규 작성, 공용 3 DB read-only 검증, active manifest
  bundle 원자 교체가 더해진다. 쪼개면 "epoch은 바뀌었는데 manifest는 구 inventory"인
  중간 상태가 저장소에 남는다.
- `V5-CM-3.5` **4.0h** — 공용 3 DB의 profile별 5-role exact ACL, core→checkpoint
  successor, 실제 login container 검증과 Backend app credential 전환을 한 보안 Gate로
  닫는다. DB별 transaction·승인·marker는 분리하되 중간 상태를 완료로 선언하지 않는다.
- `V5-CM-4.5` **3.0h** — 외부 공용 DB·Neo4j·n8n을 재생성하지 않는 팀 Compose와
  Frontend production build·Backend CORS·Kafka SASL/토픽 lifecycle·MES 선택 profile을
  하나의 배포 전 리허설 경계로 닫는다. 공용 적용은 별도 승인·증적 Gate로 남긴다.
- `V5-CM-4.7` **3.0h** — E2E Runtime 13 table의 transaction reset·action provenance·
  quiescence와 crash-safe receipt chain, 다른 2 DB의 typed full fingerprint 불변을 하나의
  파괴적 작업 Gate로 묶고 격리 PostgreSQL 장애 주입까지 실증한다.
- `V5-C-0.1` **4.0h** — Runtime 9-table Repository, ID·상태 전이, action/severity pair와
  append-only 감사를 실제 PostgreSQL 저장·동시성 경계까지 한 기반 구현으로 닫는다.
- `V5-C-2.1` **6.5h** — State 20개 계약, Tool 예약·종료 감사와 soft deadline,
  canonical 14 node·Level 1/2 분기·실 PostgreSQL checkpoint 왕복을 한 실행 골격으로 닫고,
  A production Tool merge 뒤의 실제 Tool 조립 E2E만 같은 Task의 production Gate로 남긴다.
- `V5-C-2.2` **4.0h** — DB 직렬화 기반 총·Tool별·전송 예약 예산, checkpoint 재개와
  동시 예약 경합, worker queue 포화 분류, sentinel 보존 정책을 한 실행 경계로 닫고
  hard timeout 미적용 구간을 `V5-CM-4.8` 후속 Gate에 연결한다.
- `V5-C-2.3` **4.0h** — 실제 provider model·usage를 포함한 2-round 구조화 생성과 근거
  allowlist, prediction/run의 경쟁 저장·재개·실패 usage 보존을 하나의 provenance 경계로
  닫는다.
- `V5-C-3.2` **4.0h** — action 4-table UoW, incident advisory lock·run row lock,
  같은 run 멱등·자동 조치 FAILED retry 재사용·EQP_HOLD 동일 run 복구 경계, stable request
  hash와 terminal provenance 보존을 실제 PostgreSQL 동시성·rollback 회귀까지 한 저장
  경계로 닫는다.
- `V5-C-3.3` **4.0h** — approval·action·delivery 결정 UoW, durable checkpoint 중단,
  session advisory resume lock과 DB↔checkpoint crash-window 복구를 실제 PostgreSQL
  동시성·물리 session 종료 회귀까지 한 HITL 경계로 닫는다.
- `V5-C-3.4` **4.5h** — State snapshot 저장·DB 정본 결속·checkpoint 재수화 postcondition을
  한 복구 경계로 닫고, write acknowledgement 유실과 복원 불가 상태의 fail-closed를 실제
  PostgreSQL 회귀까지 증명한다.
- `V5-C-4.1` **3.0h** — n8n workflow 3종의 graph·exact schema·raw body HMAC·Kafka offset
  경계를 repository JSON으로 고정하고 Node 22 실행 harness와 변이 회귀까지 한 정적 배포
  계약으로 닫는다. 공용 import·credential·connector 검증은 `V5-C-4.2` Gate로 분리한다.
- `V5-C-4.2` **3.5h** — 공용 n8n의 runtime inventory·exact version pin·격리 리허설·tracked
  manifest PR(repository phase)과 backup/restore 경계 아래의 image 교체·env·import·무활성
  검증(public phase)을 한 적용 Gate로 닫는다. downgrade·볼륨 초기화 0을 전제로 한다.
- `V5-C-4.3` **3.5h** — SMTP 발신 adapter를 설정 실물 정합(exact path·timeout 하한)·conditional
  전이 SQL·응답별 기록 matrix·smoke runner·실 PostgreSQL 회귀와 공용 실발송 2통·비활성
  증적까지 한 발신 경계로 닫는다. SENT/FAILED callback 확정은 `V5-C-4.4`가 소유한다.
- `V5-C-4.5` **4.0h** — MES 전용 claim/settle·WF3 응답 수렴 matrix·payload provenance·
  consumer ack-then-commit·두 consumer group 증적·offset-reset guard·격리 SASL Kafka 회귀와
  첫 공용 실왕복(e2e 전용 Backend·복원)까지 한 Kafka 경계로 닫는다. 멱등·UNKNOWN·영구
  활성은 `V5-C-4.6` 소유다.
- `V5-CM-4.3` **3.0h** — 공용 3 DB 통합 검증을 기존 full verifier·role matrix·marker
  validator의 orchestration으로 조립하고, CLI 2-mode(반복 verify·일회성 promotion)·
  CM-2.6 closure digest 정본 대조·read-only 실증까지 한 검증 Gate로 닫는다.
- `V5-C-4.4` **3.0h** — signed callback 수신을 verifier 순서·path/DTO 422 경계·401
  ErrorResponse·terminal DML+감사 UoW·멱등 duplicate·3 producer 교차·TestClient·실
  PostgreSQL 동시성 회귀까지 한 수신 경계로 닫는다. UNKNOWN·재발송 금지는 `V5-C-4.6` 소유다.
- `V5-C-4.6` **3.0h** — 두 채널의 UNKNOWN·명시 retry 저장 전이와 동시 발신 matrix,
  callback trail host bind·exact artifact verifier, 수동 recovery·영구 활성 runbook을 한
  멱등 경계로 닫는다. 공용 n8n·SMTP·Kafka 실측은 같은 Task의 public Gate로 남긴다.

---

## 3. Common — 최종 intake·bootstrap·통합

### V5-CM-1. source intake와 epoch

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-1.1 | P0 | 최종 source intake. 완료: ③ `project.zip` SHA-256와 선별 15종(9 CSV·base DDL·`master.cypher`·Generator·RAG 3종)의 **전체 SHA-256**를 등록한다. ①에서는 `03_db/01_schema.sql`·`05_scripts/load_documents.py`·`04_infra/requirements.txt`만 별도 provenance와 전체 SHA-256로 등록한다. ②와 ①의 RAG·graph·나머지 통합 schema, 참고 Backend·Frontend·`node_modules`는 제외한다 | FR-I-04, NFR-06 | — | 2.0h |
| V5-CM-1.2 | P0 | epoch 발급. 완료: `fdc_final_20260818` epoch를 발급하고 `kosa_0813` artifact와 동시 참조를 금지한다. 이전 epoch manifest·marker는 이력 디렉터리로 격리한다 | FR-I-04 | V5-CM-1.1 | 1.0h |
| V5-CM-1.3 | P0 | source manifest v4. 완료: 9개 CSV의 컬럼·행 수·typed content hash, ③ base DDL·graph·Generator·RAG 3종, ① selected schema·loader·requirements의 source/archive hash와 출처 역할을 한 manifest에 고정한다. ③에 있는 artifact를 ①에서 대체하지 못하게 하고 행 수는 기준표 실측값과 일치시킨다 | FR-I-04, NFR-06 | V5-CM-1.2 | 2.0h |
| V5-CM-1.4 | P0 | Generator 재현 검증. 완료: `gen_sample_data.py`를 격리 실행해 9개 CSV가 byte-identical로 재생성됨을 확인하고 결과를 manifest provenance에 남긴다 | NFR-06 | V5-CM-1.3 | 1.0h |
| V5-CM-1.5 | P0 | 구 epoch 격리. 완료: v4 corrected build(`dim_parameter` overlay·`seq_no`·시각 보정)를 최종 epoch 실행 경로에서 차단하고 폐기 사유·대체 Task를 기록한다 | FR-I-04 | V5-CM-1.2 | 1.0h |
| V5-CM-1.6 | P0 | 구 corrected 계열 제거. 완료: `build_corrected_dataset`·`load_corrected_base`·`load_evaluation_mock`·`corrections/`와 대응 테스트, `manifest_v3`의 `corrected_files`·`(runtime\|evaluation, corrected_base)` stage, corrected marker·`data/corrected`를 제거하고 전체 회귀를 통과한다 | FR-I-04, NFR-06 | V5-CM-2.6, V5-CM-1.3 | 3.0h |
| V5-CM-1.7 | P1 | 구 bootstrap·skip 제거. 완료: `V5-CM-2.2`가 대체한 `bootstrap_base_schema.py`·`infra/bootstrap/001_base_schema.sql`과 대응 테스트를 제거한다. `V5-CM-1.2` 사유 skip 중 해제 Task 없는 잔존은 0건이어야 하며, `test_master_cypher` 2건만 `V5-CM-2.7` 해제 대상으로 허용한다 | FR-I-04, NFR-06 | V5-CM-2.6, V5-CM-1.3, V5-CM-1.8 | 1.5h |
| V5-CM-1.8 | P0 | manifest 최종 재기준화. 완료: `manifest_v3`의 `DATASET_EPOCH`·archive 기대값을 `infra/bootstrap/dataset-epoch.json`(`fdc_final_20260818` · `e5ce2c55…dabe3`)에 맞추고, evaluation `action_history` 12행을 담을 신규 `bootstrap_stage`와 `v5_001_reference_extensions_final` migration을 `BOOTSTRAP_STAGE_CONTRACTS`에 등록한다. `verify_bootstrap_state`가 R03 logical type을 가져오는 registry를 V5 12컬럼으로 전환하고 `V5-B-1.1`이 legacy `document_corpus`를 정리한 뒤의 **runtime 22 · evaluation 13** 물리 inventory와 최종 content hash를 담은 profile manifest를 발급한다. 구 등록 manifest의 23 · 14는 폐기 epoch `kosa_0813` 값이며 `V5-CM-3.1` artifact가 `superseded_profile_inventory_counts`로만 봉인한다. 이 manifest를 직접 읽는 Text2SQL R03 column allowlist도 V5 12컬럼으로 재기준화하되, 후속 정책 확장은 `V5-D-2.1`이 소유한다. `V5-CM-3.1`이 남긴 정적 blocker 상수 2종과 짝 회귀도 함께 제거해 `apply_reference_extensions_v5.final_manifest_blockers()`의 epoch·evaluation stage·migration stage·V5 type registry·Text2SQL allowlist 5종이 모두 해소되고 빈 tuple을 반환하는 것으로 확인한다 | FR-I-04, NFR-06 | V5-CM-2.6, V5-CM-3.1, V5-B-1.1, V5-CM-1.6 | 4.0h |

### V5-CM-2. fresh bootstrap

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-2.1 | P0 | 재구축 runner 골격. 완료: host·database allowlist, epoch·fingerprint 확인, `--confirm-target`·`--change-ref`, advisory lock, 단일 transaction, `--preflight`/`--rehearse`/apply/`--register-manifests` 모드 배타를 갖춘다. 멘토 `00_load.sh` 호출 경로는 없고 모드 오류는 sanitized reason·exit 2로 끝난다 | FR-I-04, NFR-14 | V5-CM-1.3 | 2.0h |
| V5-CM-2.2 | P0 | 격리 schema rehearsal. 완료: macOS·Windows에서 같은 절차로 일회성 PostgreSQL을 기동하고 ready 대기 후 rehearsal target의 `public` schema를 비운다. ③ `03_schema_clean.sql`을 hash pin 확인 후 **base 9 table DDL로만** 원문 실행하고 종료 시 container·volume을 정리한다. POSIX 전용 lock·명령에 의존하지 않으며 생성 결과는 base 9 table, RAG·Runtime 객체는 0건이다 | FR-I-04, NFR-14 | V5-CM-2.1 | 2.0h |
| V5-CM-2.3 | P0 | 격리 profile 적재. 완료: runner가 BOM 제거 후 FK 순서로 적재하고 runtime 2 profile은 8개 CSV(action 제외), evaluation은 9개 전부를 넣는다. rehearsal 결과는 action 0 / 0 / 12다 | FR-I-04 | V5-CM-2.2 | 1.5h |
| V5-CM-2.4 | P0 | 격리 적재 검증. 완료: 9 table 행 수·typed content hash·PK 중복 0·FK 누락 0을 manifest와 대조하고 evaluation 4,538/216/46과 알람 138·51, timestamp `+09:00` 해석을 검증한다 | FR-I-04, NFR-06, NFR-13 | V5-CM-2.3 | 1.5h |
| V5-CM-2.5 | P0 | 재실행·복구 rehearsal. 완료: 같은 profile 재실행은 no-op이고 부분 실패는 단일 transaction rollback으로 되돌아간다. marker 유실은 `--recover-artifact`로만 복구하며 실패 주입 후 source hash가 보존된다 | FR-I-04, NFR-14, NFR-16 | V5-CM-2.4 | 1.0h |
| V5-CM-2.6 | P0 | 공용 PostgreSQL 전환 gate. 완료: 3 profile의 backup·restore rehearsal과 팀 change approval을 기록한 뒤 `kosa_agent_e2e` → `kosa_agent` → `kosa_text2sql` 순서로 preflight→rehearse→apply→no-op→검증한다. action 초기값은 0/0/12이며 다른 DB 변경은 0건이다. `V5-B-1.3`이 이미 적재한 `document`·`document_chunk`와 vector extension은 **보존**하고 전환 전후로 문서 3·chunk 수·`null_embedding_count` 0이 불변임을 검증한다. base 9의 `wafer` type 변경이 `v_alarm_event`에 막히므로 legacy View를 drop한 뒤 **같은 transaction 안에서 기존 shape를 유지한 2.6 호환 View를 즉시 복구**한다. 이 View는 임시 소유이며 최종 계약은 `V5-CM-3.1`이 가져간다 | FR-I-04, NFR-14, NFR-18 | V5-CM-2.5 | 2.0h |
| V5-CM-2.7 | P0 | Neo4j safe apply gate. 완료: `V5-B-3.1`의 offline parser가 ③ `master.cypher` 100개 statement·44 nodes·85 relationships fixture를 통과한 입력만 기존 destructive-safe loader로 적용한다. target fingerprint·backup/restore·팀 change approval·confirm guard 후 apply·재실행 no-op·중복 0·epoch/source hash/fingerprint marker를 검증하고 `test_master_cypher` 2건의 skip을 해제한다 | FR-B-01, FR-I-04, NFR-06, NFR-14 | V5-B-3.1 | 2.0h |

### V5-CM-3. Runtime schema

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-3.1 | P0 | Reference extension 재기준화. 완료: `V5-CM-2.6`이 임시로 복구한 호환 View를 **교체**하고 3개 DB에 **빈** `r03_alarm_history`와 `v_alarm_event`만 소유하며 `nl_query_log`·`document`·`document_chunk`·vector extension은 만들지 않는다. R03 table은 설계 §3.2의 12개 필수 컬럼과 고정 직렬화 계약을 가지며, successor는 exact V4·0행 guard 뒤 drop-and-recreate하여 base-only와 동일한 컬럼 순서·canonical comment로 수렴한다. 공용 successor R03/View의 target별 owner·ACL은 runner가 승인 pre-state대로 복원·postcheck하고 drift·`relacl IS NULL`은 mutation 전에 거부한다. pre-state comment는 증적만 남기며 R03는 canonical V5 comment, View는 canonical `NULL`을 적용한다. base-only는 creator owner를 유지하고 ACL 복원을 건너뛴다. View는 `h.wafer_id = a.wafer`로 join하며 초기 상태에서 저장 알람 189·R03 0·AlarmRef 중복 0을 검증한다. profile manifest는 발급하지 않고 task-scoped migration contract artifact만 만들며, 그 artifact는 `manifest_v3` 등록 type·등록 디렉터리에 들어가지 않는다 | FR-A-06, FR-I-04 | V5-CM-2.6 | 2.0h |
| V5-CM-3.2 | P0 | Agent Runtime migration. 완료: runtime 2개에만 설계 §3.4의 9 table을 생성한다. `action_history=0` guard, evaluation 적용 거부, legacy FK 0건, 부분 고유 인덱스를 포함한다 | FR-C-04~09 | V5-CM-3.1, V5-CM-1.8 | 2.0h |
| V5-CM-3.3 | P0 | action/severity pair guard. 완료: 명명 CHECK로 반쪽 NULL 행을 차단한다. 배포 후 16조합 중 정상 4조합만 수락됨을 실제 INSERT·rollback으로 증명한다 | FR-C-03, FR-C-07 | V5-CM-3.2 | 1.0h |
| V5-CM-3.4 | P0 | Checkpoint 초기화. 완료: runtime 2개에만 `PostgresSaver.setup()`을 one-shot 실행한다. 앱 startup의 `.setup()` 호출 0회, 재실행 시 catalog·migration version·행 수 무변경, thread 재개 smoke를 확인한다 | FR-C-04 | V5-CM-3.3 | 1.5h |
| V5-CM-3.5 | P0 | 최소권한 role. 완료: final profile manifest와 `role_core → role_checkpointed` stage별 exact inventory에 대해 app/readonly/evaluation/logger/delivery 5-role 허용·거부 matrix를 적용한다. 생성 Text2SQL은 readonly만 실행하고 Runtime app은 owner fallback 없이 `kosa_app`만 사용한다. `document`·`document_chunk`, 합성 label column, Runtime·audit·checkpoint·query-log sequence를 명시 ACL로 고정하며 signed HTTP n8n delivery role은 NOLOGIN·deny-all이다. database/schema/relation/sequence의 `PUBLIC` data-access 권한 0건과 같은 stage no-op을 확인한다 | NFR-01, NFR-02, NFR-05, NFR-19, FR-D-03 | V5-CM-3.3, V5-CM-3.4 | 4.0h |

### V5-CM-4. 공통 계약과 통합

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-4.1 | P0 | 공통 Enum·DTO·Tool envelope. 완료: `AlarmRef`, Action/Approval/Delivery/Run 상태, Fault 5-class, 오류 body를 최종 기준으로 정렬한다. Tool signature는 `get_fdc_summary(lot_hist_id)`, `get_equipment_context(chamber_id)`, `search_documents(query, model_code=None, top_k=4)`, `send_action(action_id)`, `generate_analysis_plan(question)`으로 고정한다. Tool 결과는 `ok`·`reason`과 도메인 payload만 가지며 reason prefix는 공통 계약을 참조한다. `latency_ms`는 결과에서 제외하고 실행 wrapper가 `agent_tool_call` metadata로 기록한다. 공개 승인 요청은 `APPROVED\|REJECTED`이고 내부 Enum은 adapter에서만 변환한다 | FR-I-07, NFR-09~10 | V5-CM-1.2 | 1.5h |
| V5-CM-4.2 | P0 | 감사 쓰기 계약. 완료: event enum·entity mapping·append-only helper와 트랜잭션 규칙을 제공한다. UPDATE·DELETE 경로를 만들지 않는다 | FR-D-07, NFR-05 | V5-CM-4.1 | 1.0h |
| V5-CM-4.3 | P0 | profile 통합 검증기. 완료: 공용 3 DB의 epoch·stage·table·행 수·hash·권한·marker를 한 번에 검사하고 target별 결과를 보존한다. 한 target 실패가 전체 report를 지우지 않고 다른 DB 변경은 0건이다 | FR-I-04, NFR-14, NFR-18 | V5-CM-3.5 | 3.0h |
| V5-CM-4.4 | P0 | API contract fixture baseline. 완료: 필수 public 11개(호환 9개·단일 chamber 관계 API·`POST /agent/runs`) + internal delivery 1개 + health 2개, **합계 14개**의 canonical DTO/OpenAPI 기대값과 오류·bare-array 규칙을 구현과 독립된 fixture로 고정한다. 구현된 선택 확장만 별도 fixture에 포함하고 deferred 확장은 OpenAPI 존재를 요구하지 않는다. **실제 endpoint PASS는 후속 최종 API gate에서 판정한다** | FR-I-01, FR-I-07, NFR-09~11 | V5-CM-4.1 | 1.5h |
| V5-CM-4.4-1 | P0 | **5화면 navigation 전환**. 완료: 현재 7개 메뉴(`/dashboard`·`/alarms`·`/traces`·`/agent-runs`·`/actions`·`/analytics`·`/audit-logs`)를 canonical 5영역(Dashboard·Alarm History·Agent·Documents·Ontology)으로 재구성한다. 기존 상세 route는 하위 흐름·deep link로 유지하고 독립된 제6 화면을 만들지 않는다. Text2SQL route는 선택 확장으로 분리한다 | FR-I-02 | V5-CM-4.4 | 2.0h |
| V5-CM-4.4-2 | P0 | **shared client·projection 기반**. 완료: 필수 public 11개의 공용 client와 canonical→deprecated alias serializer만 제공한다. `POST /agent/runs`는 source-aware `AlarmRef` body를 사용한다. domain 페이지가 실제 호출·상태를 소비하는 책임은 A/B/C/D Task에 두고, Common에서 `api.audit()` UI를 호출하지 않는다 | FR-I-02, FR-I-03, NFR-11 | V5-CM-4.4-1 | 1.5h |
| V5-CM-4.4-3 | P1 | alias 제거 조건. 완료: compatibility alias 목록과 모든 소비 화면의 canonical 전환 완료 조건을 문서화하고 조건 충족 시 실행할 추적 항목을 최종 gate에 연결한다 | FR-I-03 | V5-CM-4.4-2 | 1.0h |
| V5-CM-4.5 | P1 | compose·배포 통합. 완료: PostgreSQL·Neo4j·n8n은 학원 공용 외부 서비스로 환경변수 연결하고 팀 compose에는 Backend·Frontend·Kafka·MES Mock consumer만 둔다. `/api` proxy·명시 CORS Origin·고정 image tag를 적용하며 DB/Neo4j/n8n 컨테이너와 reference `00_load.sh` 호출은 0건이다 | FR-I-04, FR-I-06, NFR-02, NFR-12, NFR-15 | V5-CM-4.3, V5-C-4.2 | 3.0h |
| V5-CM-4.6 | P1 | liveness·readiness·복구. 완료: `/health`는 process 생존만 반환하고 `/health/ready`는 병렬 timeout으로 PostgreSQL epoch/schema/role·reference marker, Neo4j 44/85 marker/fingerprint, RAG 3문서·chunk·vector1024·검색 smoke, n8n, Kafka metadata·`fdc.actions`·`fdc.actions.result`와 consumer group `kosa-fdc-wf4-writeback` lag를 검사한다. lag가 5분 이상 0보다 크거나 다른 의존성이 미준비면 sanitized 503이고 process는 종료하지 않는다 | FR-I-05, NFR-02, NFR-16 | V5-CM-4.5, V5-CM-2.7, V5-B-1.4, V5-C-4.2, V5-C-4.5 | 2.0h |
| V5-CM-4.7 | P0 | E2E reset guard. 완료: host·DB·token 확인 후 `kosa_agent_e2e`의 Runtime 실행 데이터만 초기화한다. `kosa_agent`·`kosa_text2sql` 대상은 거부하고 source·reference·RAG·checkpoint schema를 보존하며 다른 DB 변경 0건을 증명한다 | NFR-14, NFR-18 | V5-CM-3.4, V5-CM-4.3 | 3.0h |
| V5-CM-4.8 | P1 | Tool hard timeout·sentinel 복구 재평가. 완료: `get_equipment_context`의 Neo4j transaction timeout과 `search_documents`·`get_fdc_summary` DB 구간의 PostgreSQL statement timeout을 실제 집행하고 8초 뒤 작업 종료 postcondition을 증명한다. in-process embedding·model 구간은 process 격리 도입 또는 soft 유지와 NFR-03 부분 미충족 기록 중 하나를 결정한다. hard 종료가 증명된 구간에 한해 sentinel 시간 기반 자동 회수를 재평가하고, Tool별 hard/soft 판정·잔여 한계를 남긴다 | NFR-03 | V5-C-2.2 | 2.0h |

### V5-CM-5. 최종 통합 gate

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-5.1 | P1 | 실제 API·산출물 sync gate. 완료: 필수 public 11개 + internal delivery 1개 + health 2개, **합계 14개**의 route/OpenAPI/contract를 baseline과 대조하고 동일 Method+Path 중복 0을 확인한다. 구현된 선택 확장만 같은 비교 집합에 넣고 deferred 확장은 OpenAPI를 요구하지 않는다. API Markdown·CSV·PDF를 같은 schema에서 재생성해 비교 집합의 path·request·response·status diff 0을 기록한다 | FR-I-01, FR-I-07, NFR-10~11 | V5-CM-4.4, V5-CM-4.4-3, V5-A-3.2, V5-B-2.3, V5-B-3.3, V5-C-4.4, V5-C-5.1, V5-D-1.2, V5-CM-4.6 | 2.0h |
| V5-CM-5.2 | P1 | 통합 E2E gate. 완료: React 5화면+FastAPI+3 DB+Neo4j+RAG+n8n SMTP+Kafka MES Mock를 `kosa_agent_e2e`에서 실행한다. 12 incident 5/4/3, 승인 전 Kafka 0, 승인·반려·UNKNOWN·중복 효과 최대 1, 화면 4상태와 label 비누수를 검증하고 다른 DB 변경 0건을 남긴다 | FR-I-01~05, NFR-16~20 | V5-CM-5.1, V5-CM-4.7, V5-A-3.4, V5-B-4.1, V5-B-4.2, V5-C-5.2, V5-C-6.1, V5-D-1.3 | 2.0h |
| V5-CM-5.3 | P1 | 최종 비기능·증적 gate. 완료: Docker·Python·Node·lockfile pin, CORS 허용/거부, `+09:00`, secret scan, DB·Neo4j·LLM·n8n·Kafka 장애 격리를 검증한다. 공용 전환을 다시 수행하거나 새 승인을 받지 않고 CM-2.6·2.7에서 생성한 backup/restore·팀 change approval 증적의 존재·대상·결과를 최종 report에 인용한다. CM-4.8의 Tool별 hard/soft 판정·종료 postcondition·잔여 미충족도 같은 report에 인용한다 | NFR-02, NFR-03, NFR-12~16 | V5-CM-5.2, V5-CM-1.6, V5-CM-1.7, V5-CM-4.8 | 2.0h |

**Common 합계: 65.5h**

---

## 4. A — Detection Full-stack

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-A-1.1 | P0 | Summary 재계산. 완료: Trace에서 `summary_data` 4,800건을 결정론적으로 재현하고 불일치 0건을 확인한다 | FR-A-01 | V5-CM-2.6 | 2.0h |
| V5-A-1.2 | P0 | evaluation 재현. 완료: point 판정으로 IN 4,538 / OOC 216 / OOS 46을 재현한다. `upper_only` parameter의 하한 미판정을 포함한다 | FR-A-02 | V5-A-1.1 | 2.0h |
| V5-A-1.3 | P0 | TRACE·SUMMARY 알람. 완료: TRACE 138·SUMMARY 51을 재현하고 저장 알람 합계 189를 확인한다. 시각 NULL 0건이다 | FR-A-02 | V5-A-1.2 | 2.0h |
| V5-A-1.4 | P0 | R03 파생·적재. 완료: 같은 chamber·parameter·recipe step에서 `chamber_wafer_cum` 오름차순 연속 3 최초 도달로 R03 3건을 결정론적으로 만들고 CM-3.1의 3개 DB 빈 table에 멱등 적재한다. 각 R03는 member wafer 3개와 TRACE AlarmRef 9개를 가지며 View는 저장 알람 189·R03 포함 192·AlarmRef 중복 0을 반환한다 | FR-A-03, FR-A-06 | V5-A-1.3, V5-CM-3.1 | 2.0h |
| V5-A-1.5 | P0 | incident 집계. 완료: 알람이 있는 `(lot_id, chamber_id)` 12개를 산출하고 참고 action 12건과 1:1임을 확인한다. R03 포함 합계 192를 검증한다 | FR-A-03, FR-A-06 | V5-A-1.4 | 1.5h |
| V5-A-2.1 | P0 | 비지도 anomaly score. 완료: LOT 단위 분리로 재현 가능한 score를 만들고 feature·seed·normalization·model version을 고정하며 Fault·metrology·Generator 누수 0건을 검증한다 | FR-A-04, NFR-08, NFR-19 | V5-A-1.5 | 2.0h |
| V5-A-2.2 | P0 | score 경계 고정. 완료: score가 조치·incident·승인 게이트에 전달되지 않음을 계약 테스트로 고정한다. score 없이도 규칙 처리가 동일하다 | FR-A-05 | V5-A-2.1 | 1.0h |
| V5-A-2.3 | P0 | 합성 라벨 격리. 완료: `fault_code`를 평가 loader에서만 읽고 Runtime repository 타입과 분리한다. 모델 feature·threshold·Tool·API에 사용하지 않음을 allowlist·query·payload 테스트로 고정한다 | FR-A-08, NFR-19 | V5-A-2.1 | 1.5h |
| V5-A-2.4 | P1 | Detection 평가 artifact·holdout. 완료: 공개 라벨을 읽기 전에 후보 model·feature·threshold와 prediction hash를 고정한 뒤 격리 synthetic holdout을 1회 평가하고 같은 revision 재튜닝을 금지한다. metrology 48/600, 합성 label metadata와 운영 성능 비주장을 기록한다 | FR-A-08, FR-A-09, NFR-19 | V5-A-2.3 | 2.0h |
| V5-A-3.1 | P0 | `GET /alarms`. 완료: 최종 필터·`(source, alarm_id)`·189/192 계약, 안정 정렬과 offset 포함 시간을 제공한다 | FR-A-06, NFR-11, NFR-13 | V5-A-1.5, V5-CM-4.1 | 2.0h |
| V5-A-3.2 | P0 | `GET /trace`·`GET /parameters`. 완료: 참고 React 호환 응답과 canonical field를 단일 boundary projection으로 제공하고 안정 정렬·빈 배열 계약을 지킨다 | FR-A-06, NFR-11, NFR-13 | V5-A-3.1 | 1.5h |
| V5-A-3.2-1 | P0 | `get_fdc_summary(lot_hist_id)` Tool. 완료: 단일 `lot_hist_id`로 summary·evaluation·5선과 준비된 경우에만 nullable score provenance를 반환하고 Fault GT·action 권고를 제외한다. 모델 artifact가 없어도 성공하며 성공·실패·0건·timeout은 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따른다 | FR-A-05, NFR-09, NFR-19 | V5-A-1.5, V5-CM-4.1 | 1.5h |
| V5-A-3.3 | P1 | 화면 1 Dashboard. 완료: KPI·추이·상위 parameter를 실제 API로 연결하고 Loading·Error·Empty·Success를 component test로 구분한다 | FR-A-07, FR-I-02, NFR-17 | V5-A-3.2 | 2.0h |
| V5-A-3.4 | P1 | 화면 2 Alarm History. 완료: 목록·필터·상세와 source-aware deep link를 실제 API에 연결한다. 분석 실행 버튼은 선택 AlarmRef를 `POST /agent/runs`에 보내고 202·409·422·503 상태와 생성 run deep-link를 처리한다. Loading·Error·Empty·Success를 검증한다 | FR-A-07, FR-I-02~03, NFR-17 | V5-A-3.3, V5-C-5.1 | 2.0h |
| V5-A-3.5 | P1 | 호환 필드 adapter. 완료: 참고 React 축약 필드를 한시 지원하고 canonical field로 교체하는 경로를 남긴다 | FR-I-03 | V5-A-3.2 | 1.0h |
| V5-A-4.1 | P1 | Detection 회귀. 완료: 재계산·알람·R03·incident·Tool·label non-leakage를 fixture로 고정하고 CI에서 재현한다 | NFR-06, NFR-09, NFR-19 | V5-A-2.4, V5-A-3.2-1 | 2.0h |

**A 합계: 28.0h**

---

## 5. B — Knowledge Full-stack

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-B-1.1 | P0 | RAG 스키마 단일 소유. 완료: ① `03_db/01_schema.sql`의 `vector` extension·`document`·`document_chunk`만 3개 DB에 생성한다(`embedding vector(1024)`). `pg_trgm`·`document_corpus`·corpus revision과 ①의 나머지 table은 채택하지 않는다. `kosa_text2sql`에 남은 legacy `document_corpus`도 이 Task가 제거해 3 DB 물리 inventory를 **runtime 22 · evaluation 13**으로 확정한다. `PUBLIC` 권한과 Common migration 중복 객체는 0건이다. **두 table의 role별 explicit GRANT는 `V5-CM-3.5`가 소유한다** — 이 Task는 schema 생성과 `PUBLIC` revoke까지만 한다(구현리뷰 18차 필수 1: `CM-3.5`를 선행으로 두면 `CM-1.8 → B-1.1 → CM-3.5 → CM-3.2 → CM-1.8` 순환이 생긴다) | FR-B-02, FR-B-05, NFR-02 | V5-CM-3.1 | 1.0h |
| V5-B-1.2 | P0 | RAG 문서 기능 정합. 완료: 불변 원본은 ③ `project.zip`·CM-1.3 hash로 보존하고 저장소에는 별도 corrected Markdown 3종만 정본으로 둔다. 고정 EQP upstream, score·metrology·반복·하류·후속 정상 기반 조치 상하향, 구 10건 서술을 제거한다. R03·R01·Fault 후보·PH/ET 범위를 최종 계약에 맞추고 Markdown 구조와 금지 문구 검사를 통과한다. 자동 marker·상세 provenance 검증은 B-1.4로 분리한다 | FR-B-02, NFR-06 | V5-CM-1.3 | 1.5h |
| V5-B-1.3 | P0 | RAG 기능 적재. 완료: ① `load_documents.py`의 청킹·임베딩 로직을 최소 adapter로 재사용해 B-1.2 corrected 경로만 받는다. 명시 DSN과 대상 DB allowlist(`kosa_agent`, `kosa_agent_e2e`)를 강제하고 기본 DSN·원본 fallback·`--reset`을 거부한다. DB별 한 transaction으로 3문서와 canonical `<document_id>:cs1:<4자리>` chunk를 적재하며 `BAAI/bge-m3`·1024, 문서 3건·chunk 1건 이상/문서·embedding NULL 0·대표 검색 smoke를 확인한다. 이 완료 뒤 B-2 기능 구현은 운영 검증을 기다리지 않고 시작할 수 있다 | FR-B-02, FR-B-05, NFR-14 | V5-B-1.1, V5-B-1.2, V5-CM-3.5 | 1.5h |
| V5-B-1.4 | P1 | RAG 운영 검증 강화. 완료: B-1.3 적재를 `kosa_text2sql`까지 확장해 3 DB를 정렬하고 권한·live fingerprint·중복 0·idempotent no-op을 검증한다. source/corrected SHA-256·정정 사유, cs1 contract hash, 고정 model revision·weights hash·dimension, 3 document ID·chunk 수·검색 smoke를 DB별 COMMITTED marker에 marker-last로 기록하며 readiness가 live DB와 대조할 수 있게 한다 | FR-B-02, FR-B-05, NFR-06, NFR-14 | V5-B-1.3, V5-B-2.1 | 2.0h |
| V5-B-2.1 | P0 | DocumentSearchRepository·Service. 완료: pgvector 검색을 구현하고 `query`·`model_code`·`top_k`를 지원한다. embedding 모델은 process당 1회 생성해 재사용한다(singleton). **API와 Tool이 이 Service를 공유하며 검색 로직을 중복 구현하지 않는다** | FR-B-04 | V5-B-1.3 | 2.0h |
| V5-B-2.2 | P0 | `search_documents(query, model_code=None, top_k=4)` Tool. 완료: exact signature로 `DocumentSearchService`를 호출해 chunk·score·근거 ID를 반환한다. 0건·timeout·오류는 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따른다 | FR-B-04, NFR-09 | V5-B-2.1 | 1.5h |
| V5-B-2.3 | P0 | `POST /documents/search`. 완료: Documents 화면이 쓰는 검색 API로, Tool과 **동일한** `DocumentSearchService`를 재사용한다. 실제 근거 내용(document·chunk)을 반환한다 | FR-B-04 | V5-B-2.1 | 1.0h |
| V5-B-3.1 | P0 | final `master.cypher` offline parser·fixture. 완료: ③ 원본을 DB 접속 없이 destructive 문장과 seed 100개 statement로 분리·파싱하고 node 44·relationship 85, label/type·business key·방향·중복 0을 고정 fixture와 단위 테스트로 검증한다. source hash 불일치·미지원 statement는 safe apply 전에 실패한다 | FR-B-01, NFR-06 | V5-CM-1.3 | 2.0h |
| V5-B-3.2 | P0 | GraphService·`get_equipment_context(chamber_id)` Tool. 완료: CM-2.7이 적용·marker한 graph를 읽는 GraphRepository·Service를 만들고 exact signature로 장비·모델·AREA·Process Step·인접 Step·파라미터·형제 chamber와 stable relation/graph provenance를 반환한다. 0건·timeout·오류는 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따르며 elementId·고정 설비 upstream·LOT routing 추정은 노출하지 않는다 | FR-B-03, NFR-09 | V5-CM-2.7 | 1.5h |
| V5-B-3.3 | P0 | 단일 `GET /relations/chambers/{chamber_id}`. 완료: CM-2.7 marker가 가리키는 graph의 chamber 중심 read-only 응답을 B-3.2 `GraphService`에서 만들고 Neo4j 자격증명·Cypher·elementId를 노출하지 않는다. 같은 Method+Path의 다른 DTO를 만들지 않으며 노드 타입 확장은 같은 응답 shape의 `/relations/{node_type}/{node_id}`로만 확장한다 | FR-B-06, NFR-02, NFR-11 | V5-CM-2.7, V5-B-3.2 | 1.5h |
| V5-B-4.1 | P1 | 화면 4 Documents. 완료: `POST /documents/search`를 실제 연동해 근거·deep link와 Loading·Error·Empty·Success를 표시한다 | FR-B-06, FR-I-02, NFR-17 | V5-B-2.3 | 2.0h |
| V5-B-4.2 | P1 | 화면 5 Ontology. 완료: chamber를 선택해 단일 관계 API의 장비·모델·AREA·Process Step·인접 Step·파라미터를 시각화하고 Loading·Error·Empty·Success를 검증한다. Neo4j Browser iframe·비밀정보 노출은 0건이다 | FR-B-06, NFR-02, NFR-17 | V5-B-3.3 | 2.0h |
| V5-B-4.3 | P1 | 최소 검증·평가. 완료: B-1.4 운영 검증 artifact, RAG 검색 contract·embedding singleton·Neo4j 44/85·chamber 관계 fixture를 검증하고 **Recall@4 ≥ 0.80, MRR ≥ 0.70, 관계 질문 100%**와 실패 사례를 artifact에 기록한다. 이 Task는 B-2/B-3/B-4 기능 구현의 착수 gate가 아니라 최종 인수 gate다 | FR-B-07 | V5-B-1.4, V5-B-2.2, V5-B-3.2 | 2.0h |

**B 합계: 21.5h** (P0 기능 13.5h / P1 화면·운영 검증 8.0h, P2 없음)

> 출처 규칙은 `docs/reference/배포패키지_기준.md`를 따른다 — **③에 있으면 ③, ③에 없는 것만 ①**.
> `document`·`document_chunk` 스키마와 `load_documents.py`·`bge-m3` 1024만 ①에서 가져오고,
> RAG 문서 3종과 `master.cypher`는 ③이다(①과 내용이 다르다 · 기준 문서 §3.1).

---

## 6. C — Agent·HITL·n8n·Kafka Full-stack

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-C-0.1 | P0 | Runtime Repository. 완료: 설계 §3.4의 9 table Repository와 ID 계약을 만들고 Common append-only helper로 같은 업무 transaction 안에 감사를 기록한다. Tool 결과와 분리된 실행 횟수·상태·`latency_ms`를 `agent_tool_call` metadata로 저장하며 `action`·`severity`는 항상 함께 채운다 | FR-C-07, NFR-05, NFR-09 | V5-CM-3.3, V5-CM-4.2 | 4.0h |
| V5-C-0.2 | P0 | thread·checkpoint 계약. 완료: `agent_run_id`와 독립인 thread UUID, 저장·interrupt·동일 thread 재개 fixture를 만든다 | FR-C-04 | V5-CM-3.4, V5-C-0.1 | 1.5h |
| V5-C-1.1 | P0 | incident 해석. 완료: source-aware `AlarmRef`를 `(lot_id, chamber_id)`로 묶고 대표 알람을 `occurred_at ASC, source priority, alarm_id ASC`로 결정한다 | FR-C-01 | V5-A-1.5, V5-C-0.1 | 2.0h |
| V5-C-1.2 | P0 | 실제 routing 결합. 완료: `lot_history` LOT/WAFER routing과 B `GraphService`·`get_equipment_context`의 Process Step 인접을 결합한다. public graph API를 내부 Tool 대신 호출하지 않으며 불일치는 `route_consistency=false`로 보존한다 | FR-C-10 | V5-C-1.1, V5-B-3.2 | 2.0h |
| V5-C-1.3 | P0 | 중복 실행 방지. 완료: 동일 incident 동시 요청에서 활성 run 1개만 만들고 처리 완료 incident는 재선택하지 않는다 | FR-C-09, FR-C-14 | V5-C-1.1 | 1.5h |
| V5-C-2.1 | P0 | LangGraph Level 1·2 골격. 완료: load_incident→A/B Tool 수집→가설→규칙 조치→저장→delivery/HITL→finalize를 같은 State·Tool로 구성하고 설정만으로 고정 흐름/조건 분기를 전환한다. fixture에서 Level별 완료와 `agent_tool_call` 기반 호출 수 차이를 고정한다 | FR-C-02 | V5-C-1.2, V5-A-3.2-1, V5-B-2.2, V5-B-3.2 | 6.5h |
| V5-C-2.2 | P0 | Tool 예산. 완료: 총 8회·동일 Tool 재시도 상한·전송 예약을 HITL 중단·재개 전후 누적 적용하고 checkpoint·DB에서 복원한다. 읽기 Tool은 caller 대기 soft 8초를 적용하고 worker 포화·queue 대기를 실제 Tool timeout과 별도 code로 구분한다. hard 상한의 실제 집행·종료 검증은 `V5-CM-4.8`에 귀속한다. finalize 실패 예약 sentinel은 자동 회수·삭제·차감하지 않고 row를 보존해 예산에 계속 포함하며, hard 종료가 확보된 구간의 회수 가능성만 `V5-CM-4.8`에서 재평가한다 | FR-C-08, NFR-03 | V5-C-2.1 | 4.0h |
| V5-C-2.3 | P0 | 원인 가설. 완료: `FOC\|RFM\|MFD\|TMD\|OTH` 구조화 출력과 실제 AlarmRef·chunk·relation 근거 인용을 생성하고 실제 LLM input/output token·model·prompt version을 run·prediction에 기록한다. `NRM`과 합성 라벨·Generator FAULTS는 query·State·Tool·prompt에 넣지 않는다 | FR-C-07, FR-C-15, NFR-19 | V5-C-2.2 | 4.0h |
| V5-C-3.1 | P0 | `decide_action`. 완료: SUMMARY OOC-only → MONITORING, TRACE OOS → WARNING, strict R03 → EQP_HOLD의 3단계 순수 규칙 함수를 만든다. LLM·score·metrology를 입력에서 제외한다 | FR-C-03 | V5-C-2.3 | 2.0h |
| V5-C-3.2 | P0 | action 생성 transaction. 완료: incident advisory lock→run row lock 아래 `action_history`·CREATED/REUSED link·approval·delivery와 policy provenance를 한 트랜잭션에서 만들고 incident당 유효 action 1건을 보장한다. `request_hash`는 stable identity의 raw 64 hex이며 같은 run 재호출과 자동 조치의 FAILED retry를 멱등 처리한다. EQP_HOLD는 approval의 상태·소유 run을 검증하며 새 run에 기존 approval을 재사용하지 않는다 | FR-C-14 | V5-C-3.1 | 4.0h |
| V5-C-3.3 | P0 | HITL 승인. 완료: EQP_HOLD bundle과 `WAITING_APPROVAL`을 같은 transaction에 저장하고 승인요청 email node 뒤 durable checkpoint에서 중단한다. 승인·action·MES delivery·감사를 한 조건부 UoW로 결정하며 session advisory lock 아래 동일 thread를 재개한다. DB↔checkpoint crash window는 같은 run catch-up으로 복구하고 checkpoint 상실은 fail-closed한다 | FR-C-04, FR-C-05 | V5-C-3.2, V5-C-0.2 | 4.0h |
| V5-C-3.4 | P0 | HITL checkpoint 상실 재수화. 완료: `persist_action` 직전 State snapshot을 EQP_HOLD bundle·`WAITING_APPROVAL`과 같은 transaction에 저장한다. checkpoint가 없을 때 DB run·snapshot provenance·prediction·bundle을 결속 검증해 `approval_email` 앞에 복원하고 기존 catch-up으로 `hitl_interrupt` 앞에서 중단한다. `start_incident_run`·외부 Tool·LLM·새 action 호출 0회, write 불확실 postcondition과 복원 불가 상태의 sanitized fail-closed를 증명한다 | FR-C-04, FR-C-14 | V5-C-3.3 | 4.5h |
| V5-C-4.1 | P0 | n8n workflow 제작. 완료: delivery·write-back용 `WF2-notify-email`·`WF3-mes-hold`·`WF4-result-writeback` JSON 3종만 `deploy/n8n/`에 둔다. 실행 시작은 source-aware `POST /agent/runs`가 소유하며 source-less `WF1-alarm-to-agent`는 만들지 않는다. raw body HMAC·timestamp 검증, `request_hash` 멱등성, Kafka key=`action_id`, channel=`MES_MOCK` 계약을 workflow fixture로 고정하고 secret·credential은 포함하지 않는다 | FR-C-12, NFR-02, NFR-20 | V5-C-3.3 | 3.0h |
| V5-C-4.2 | P0 | **공용 n8n import·연결**. 완료: workflow 3종을 학원 공용 n8n에 import해 typeVersion·crypto/env 허용·HTTP output 형상과 credential connector를 확인하고, credential·webhook URL·승인된 SMTP sender를 공용 환경에서 주입한다. Kafka Trigger의 top-level `resolveOffset=onSuccess`·`eachBatchAutoResolve=false`·`errorRetryDelay=5000`과 Producer의 `acks=true`·`timeout=10000`이 pin 소스·import/export에서 보존되는지 확인한다. connector-level stub smoke 뒤 모두 비활성화하며 실제 SMTP·Backend callback·Kafka 왕복과 영구 활성은 `V5-C-4.3`~`4.6`이 소유한다. 팀 compose의 n8n 컨테이너는 0건이다 | FR-C-12, FR-I-04, NFR-02 | V5-C-4.1 | 3.5h |
| V5-C-4.3 | P0 | SMTP delivery. 완료: WARNING 이메일 1회, EQP_HOLD 승인요청 이메일 1회를 서명 webhook으로 발송하고 실패·timeout을 기록한다. 동기 호출을 유지하면 Backend→n8n timeout을 workflow 최악 20초보다 큰 **25초 이상**으로 고정한다. 비동기로 바꾸면 202 acceptance와 DB delivery 조회를 정본으로 계약한다 | FR-C-06, FR-C-12 | V5-C-4.2 | 3.5h |
| V5-C-4.4 | P0 | write-back callback. 완료: `POST /internal/actions/{action_id}/delivery`가 timestamp·HMAC 서명·300초 replay window를 검증하고 channel별 상태를 갱신한다 | FR-C-06 | V5-C-4.3 | 3.0h |
| V5-C-4.5 | P0 | Kafka MES Mock. 완료: 승인된 EQP_HOLD만 n8n Kafka Producer로 `fdc.actions`에 발행하고, MES Mock consumer 결과를 `fdc.actions.result` → write-back으로 반영한다. 승인 전 발행 0건·반려 시 발행 0건을 음성 테스트로 고정한다. execution 저장이 꺼진 WF4 malformed discard는 직접 조회할 수 없으므로 의심 record 전후 consumer offset 비교로 처리 여부를 확인하고, callback 실패 시 미해결 offset과 broker retention 안의 offset-reset 복구 절차를 runbook으로 증명한다 | FR-C-06, FR-C-12 | V5-C-4.4 | 4.0h |
| V5-C-4.6 | P0 | 채널 멱등성. 완료: EMAIL·MES_MOCK 각각 `(action_id, channel)` 외부 효과 최대 1회, 동일 hash 재수신 동일 결과, 다른 hash 409, 응답 유실 `UNKNOWN`·자동 재발송 0회를 n8n·Kafka 경로에서 검증한다. CM-4.6 자동 lag 감지가 준비되기 전에는 runbook의 consumer group `kosa-fdc-wf4-writeback` lag 확인을 영구 활성 직전·직후 수동 수행하며, 확인할 수 없으면 영구 활성은 BLOCKED다 | FR-C-06, NFR-20 | V5-C-4.4, V5-C-4.5 | 3.0h |
| V5-C-4.6-1 | P0 | `send_action(action_id)` Tool. 완료: 단일 `action_id`의 저장된 delivery plan·승인 상태를 검증해 실행 가능한 EMAIL·MES_MOCK adapter만 호출하고 조치를 재결정하지 않는다. 예약은 `AuditedToolExecutor`의 공용 예산 guard를 경유하고 `reserve_tool_call()`을 직접 호출하지 않는다. graph node는 공용 nonterminal Tool 수집 경계를 경유하며 예산 차단으로 run을 FAILED 처리하지 않는다. 0건·정책 거부·timeout·중복은 공통 `ok`·`reason`·빈 deliveries 계약과 공통 reason prefix를 따른다 | FR-C-06, NFR-09, NFR-20 | V5-C-4.6 | 1.5h |
| V5-C-5.1 | P0 | 필수 API 5종. 완료: `GET /agent/runs`, `POST /agent/runs`, `POST /agent/ask`, `GET /approvals`, `POST /approvals/{approval_id}/decision`을 canonical DTO로 제공한다. 실행 시작은 `{alarm:{source,alarm_id}}`만 받아 202로 run을 만들고, run 응답의 `deliveries`는 action link에서 public `EMAIL\|MES` projection으로 만든다. 목록은 안정 정렬·bare array, 공개 승인 body는 `APPROVED\|REJECTED`이며 Chat은 A/B Tool만 사용한다 | FR-C-01, FR-C-05, FR-I-03, FR-I-07, NFR-10~11, NFR-19 | V5-C-3.3, V5-C-2.3, V5-C-1.3, V5-B-2.2, V5-CM-4.1 | 2.0h |
| V5-C-5.2 | P1 | 화면 3 Agent 조립. 완료: 실행·승인·action·delivery와 A/B 근거 deep link를 연결하고 D가 소유한 감사 subview를 탭에 조립한다. `api.audit()` 구현을 중복하지 않으며 Loading·Error·Empty·Success를 검증한다 | FR-C-13, FR-I-02, NFR-17 | V5-C-5.1, V5-D-1.3 | 2.0h |
| V5-C-6.1 | P0 | golden flow E2E. 완료: `kosa_agent_e2e`의 incident 12개에서 MONITORING 5/WARNING 4/EQP_HOLD 3, n8n EMAIL, 승인 전 Kafka 0, 승인 후 MES Mock, 중복 실행·동시 승인·UNKNOWN·복구를 `send_action` 경유로 검증하고 동일 fixture의 Level 1·2 완료율·실제 Tool 호출·wall-clock 지연·LLM token 비교를 기록한다 | FR-C-02, FR-C-09, NFR-04, NFR-18, NFR-20 | V5-C-4.6-1, V5-C-5.1, V5-C-3.4, V5-CM-4.7 | 2.0h |
| V5-C-6.2 | P1 | Fault 5-class 평가. 완료: runtime·prompt·Tool 비노출 prediction hash를 먼저 고정하고 단일 non-NRM TRACE incident 7건의 Accuracy·Macro-F1·class별 Precision/Recall/F1·근거 유효율을 계산한다. SUMMARY-only 5건은 `NO_INJECTED_FAULT`, mixed는 `AMBIGUOUS_LABEL`로 제외하고 합성 GT metadata 4종·분모·제외 사유를 기록한다 | FR-C-15, NFR-19 | V5-C-6.1, V5-A-2.3 | 2.0h |
| V5-C-7.1 | P2 | Level 3 ReAct 비교 | FR-C-11 | V5-C-6.2 | 2.0h |

**C 합계: 69.5h** (P2 2.0h 제외)

---

## 7. D — 감사·선택 확장 Analytics

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-D-1.1 | P0 | 감사 read model. 완료: `audit_log`를 직접 조회하고 `action_history`에서 사후 합성하지 않는다. `occurred_at DESC, audit_id DESC` 안정 정렬을 적용한다 | FR-D-07, NFR-05 | V5-CM-4.2, V5-CM-3.2 | 1.5h |
| V5-D-1.2 | P0 | `GET /audit-logs`. 완료: event·actor·entity·기간 필터와 `occurred_at DESC, audit_id DESC` 정렬의 **bare array**를 반환하고 `total=items.length`로 해석한다. paged response·전체 집계는 별도 선택 확장 path에서만 제공하며 UPDATE·DELETE는 만들지 않는다 | FR-D-07, NFR-05, NFR-11 | V5-D-1.1 | 1.5h |
| V5-D-1.3 | P1 | 화면 3 감사 subview. 완료: D가 `api.audit()`를 실제 소비해 필터·정렬·상세와 Loading·Error·Empty·Success를 구현하고 C는 이 subview를 조립만 한다 | FR-D-07, FR-I-02, NFR-17 | V5-D-1.2 | 2.0h |
| V5-D-2.1 | P1 | schema allowlist·pool. 완료: `V5-CM-1.8`이 재발급한 final manifest-backed R03 12컬럼을 기반으로 table/column allowlist 정책을 소유하고 runtime readonly·evaluation readonly pool을 분리한다. DSN fallback 0건 | FR-D-03, NFR-01 | V5-CM-3.5 | 2.0h |
| V5-D-2.2 | P1 | SQL 안전 검증(선택 확장). 완료: 생성 SQL과 사용자 수정 SQL 모두 같은 단일 SELECT·AST·allowlist·위험 함수·다중 문장·LIMIT 500 정책으로 재검증하며 거부 fixture는 실행 0건과 사유를 반환한다 | FR-D-02, FR-D-09, NFR-07 | V5-D-2.1 | 2.0h |
| V5-D-2.3 | P1 | `generate_analysis_plan(question)` Tool·실행(선택 확장). 완료: 단일 `question`으로 SQL·metric·group_by·table/bar/line/histogram 계획을 만들고 검증기를 통과한 경우만 실행한다. 정책 거부·형식 오류·timeout은 공통 `ok`·`reason`·빈 payload 계약과 공통 reason prefix를 따른다 | FR-D-01, FR-D-04, NFR-09 | V5-D-2.2 | 2.0h |
| V5-D-2.4 | P1 | 질의 이력 **(선택 확장·evaluation-only)**. 완료: `nl_query_log`와 최소권한 writer를 `kosa_text2sql`에만 만들고 성공·정책 거부·실행 오류를 기록한다. runtime·E2E DB에는 table·write가 0건이며 log pool은 SQL 실행 권한을 갖지 않는다 | FR-D-05, NFR-01 | V5-D-2.3 | 1.5h |
| V5-D-2.5 | P1 | 선택 확장 UI·평가. 완료: 5화면과 분리된 route에서 Loading·Error·Empty·Success를 구분하고 미구현·장애 시 기본 5화면은 정상 동작한다. final 질문셋 12건 이상 중 10건 이상 정답을 기록한다 | FR-D-06, FR-D-08, NFR-17 | V5-D-2.4 | 2.0h |
| V5-D-3.1 | P2 | MCP 서버 노출 | FR-D-10 | V5-D-2.5 | 1.5h |

**D 합계: 14.5h** (P2 1.5h 제외)

---

## 8. 적용 순서와 게이트

```text
1  V5-CM-1.1~1.5    ③·① 선별 intake · epoch · manifest · Generator 검증
2  V5-CM-2.1~2.5    격리 PostgreSQL rehearsal · rollback/no-op 검증
   V5-B-3.1         CM-1.3 뒤 final master.cypher offline parser·100 statement·44/85 fixture 검증
3  V5-CM-2.6~2.7    팀 승인 후 공용 3 profile 전환 · B-3.1 통과 입력의 Neo4j safe apply
4  V5-CM-3.1        Reference extension 재기준화 · task-scoped migration contract artifact
5  V5-B-1.1         RAG schema 생성 · PUBLIC revoke · legacy document_corpus 제거
                    → 물리 inventory를 runtime 22 · evaluation 13으로 확정한다.
                    **9번보다 먼저 실행하는 유일한 도메인 Task다** — CM-1.8이 이 결과를
                    선행으로 요구한다(구현리뷰 18차 필수 1 · 19차 필수 1).
6  V5-CM-1.6        구 corrected 계열 제거 — 구현·전용 테스트·`manifest_v3` corrected surface·
                    local artifact를 걷어내고 retirement tombstone만 남긴다.
                    **CM-1.8보다 먼저 한다** — 최종 epoch 전환을 먼저 하면 어차피 삭제할
                    corrected 테스트를 고치는 낭비가 생긴다(CM-1.8 구현 중 실측).
7  V5-CM-1.8        manifest 최종 재기준화 — epoch·stage·type registry·profile manifest 발급
                    (B-1.1 확정 뒤 runtime 22 · evaluation 13 live inventory로 발급한다)
8  V5-CM-3.2~3.5    Runtime migration · pair guard · checkpoint · 최소권한 role
                    (CM-3.5가 B-1.1이 만든 document·document_chunk의 explicit GRANT도 적용)
9  V5-CM-4.1~4.4    공통 DTO·감사 helper·profile verifier·API fixture baseline
10 나머지 A·B·C·D   실제 dependency가 열린 Task부터 병렬 구현
                    (B-1.1은 5번에서 끝났고, B-1.3 적재는 CM-3.5 뒤다)
11 V5-CM-4.4-1~4.7  5화면 shell·shared client·compose·readiness·E2E reset
12 V5-CM-5.1        실제 API/OpenAPI와 MD·CSV·PDF sync gate
13 V5-CM-5.2        React+FastAPI+3DB+Neo4j+RAG+n8n+Kafka 통합 E2E gate
14 V5-CM-4.8        Tool hard timeout·sentinel 복구 재평가
15 V5-CM-5.3        비기능·기존 backup/restore·승인·Tool timeout 증적 확인 gate

   V5-CM-1.7       구 bootstrap·skip 제거. **CM-1.8 뒤에 한다** — CM-1.8이
                   `bootstrap_base_schema.BASE_COLUMNS`를 base final type source로 쓰므로
                   먼저 지우면 참조를 잃는다.
```

단일한 “전원 착수 gate”는 두지 않는다. 역할별 착수·완료 시점은 아래 요약 숫자가 아니라 각 Task의
명시적 `선행` DAG를 기준으로 판단한다. 독립 선행이 열린 Task는 다른 역할의 전체 완료를 기다리지
않고 병렬 수행한다.

공용 DB 적용은 `kosa_agent_e2e` → `kosa_agent` → `kosa_text2sql` 순서로만 수행하고, 각 단계에서
preflight → rehearse → apply → 재실행 no-op → 검증을 통과해야 다음 DB로 넘어간다.

## 9. 사용 금지 기준

다음은 구현·테스트·평가 gate 입력으로 사용하지 않는다.

- `kosa_0813` epoch의 manifest·marker·수치와 corrected build 산출물
- TRACE 126 / SUMMARY 47 / evaluation OOS 42·IN 4,542 / action 10건 또는 48건
- Neo4j 38 nodes · 81 relationships
- `fault_code` 600건 전부 `NRM`이라는 전제, 공개 Fault GT 부재 전제
- anomaly score 기반 조치 상향·incident 생성·승인 게이트
- 최종 패키지 Markdown의 6화면, AREA당 설비 2대 서술
- **교육생 배포패키지(①)의 RAG 문서 3종·`master.cypher`** — ③과 내용이 다르다.
  ①에서 가져오는 것은 `01_schema.sql`의 `document`·`document_chunk`·vector 부분,
  `load_documents.py`, embedding dependency를 고정한 `requirements.txt`, `BAAI/bge-m3`·1024뿐이다
  (`docs/reference/배포패키지_기준.md`)
- ①의 통합 스키마(`fdc_alarm`·`fdc_summary`·`dim_*`·`agent_run` 등)

## 10. 확정·미결·명시적 defer

| 항목 | 상태 | 결정 시점 |
|---|---|---|
| 임베딩 provider·model·차원 | **확정:** ① `BAAI/bge-m3`·1024, process singleton. 정확한 model revision·weights hash gate는 기능 구현을 막지 않고 B-1.4에서 닫는다 | `V5-B-1.3`·`V5-B-2.1`·`V5-B-1.4` |
| Kafka broker 운영 위치 | **확정:** 팀 compose의 Kafka·MES Mock. PostgreSQL·Neo4j·n8n만 공용 외부 서비스 | `V5-CM-4.5` |
| Text2SQL 활성 여부 | 선택 확장. 필수 인수 기준에 미포함 | 일정 여유에 따라 |
| v4 corrected/bootstrap 코드 삭제 | `V5-CM-1.6`·`V5-CM-1.7`로 분리 | 공용 profile 전환 뒤, 최종 gate 전 |
| compatibility alias 제거 | `V5-CM-4.4-3`이 조건을 고정하고 최종 API gate가 충족 여부를 기록 | 모든 화면 canonical 전환 후 |
| ~~`V5-CM-1.2` skip 중 graph 2건(`test_master_cypher`)~~ | **해제 완료.** `V5-B-3.1`(#106)이 offline parser·fixture를 넣으면서 literal skip이 0이 됐고, `V5-CM-2.7`이 최종 artifact 재기준화와 safe apply로 계약을 닫았다 | 해소 |
| Ontology 화면 조회 범위 | chamber 기준으로 구현(`V5-B-3.3`). 노드 타입 확장은 `GET /relations/{node_type}/{node_id}` 형태로 흡수하며 응답 스키마를 바꾸지 않는다 | 필수 5화면 완료 후 |
| 최종 epoch profile manifest 발급 | **확정:** `V5-CM-3.1`은 발급하지 않는다. `manifest_v3` epoch 상수·evaluation stage·R03 type registry·V5 migration 등록·Text2SQL manifest-backed R03 allowlist 다섯 공백을 `V5-CM-1.8`이 닫은 뒤 발급한다. 공백은 `final_manifest_blockers()`가 코드로 센다 | `V5-CM-1.8` |
| FR-B-08 하이브리드 검색 | **P2 미편성·명시적 defer.** 필수 벡터 검색·Recall/MRR gate 이후 일정 여유가 있을 때만 새 Task로 등록 | 필수 B 13 Task 완료 후 |
