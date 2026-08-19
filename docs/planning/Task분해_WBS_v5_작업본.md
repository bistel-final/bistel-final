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
| DB 이름 | `kosa_agent` · `kosa_agent_e2e` · `kosa_text2sql` **유지** | 요구사항 §321-323, 설계 §2.4 |
| 전환 방식 | **fresh 재구축.** 옛 epoch 스키마를 비우고 최종 base DDL로 새로 만든다 | 최종 CSV가 현재 스키마에 적재 불가(§1.2) |
| DDL 소유 | 멘토님 `sample/schema/03_schema_clean.sql`을 **그대로** 사용. 우리가 DDL을 쓰지 않는다 | SHA `4a437efc…` pin |
| 실행기 | 적재·검증·guard는 **우리 runner**가 소유 | 멘토 `00_load.sh`는 단일 DB·initdb 훅·재실행 불가·guard 없음 |
| 로컬 개발 | 멘토님 `docker-compose.yml` + `deploy/postgres-init/00_load.sh` **그대로** 사용 | 팀원 로컬은 추가 작업 0 |
| Runtime schema | **팀 소유.** 최종 DDL은 base 9 table만 만든다 | 설계 §3.4, 역할 §5.1 |
| 임베딩 | 벡터 검색 채택. provider·model·차원은 **B가 구현 단계에서 확정** | 설계 §5.3, 역할 §7.1 |
| n8n | workflow 4종은 **팀 산출물**, compose에 n8n 서비스 추가 | 설계 §7.3, 역할 §8.1 |
| Kafka | **필수 범위. C 담당**(역할 §8 제목·§8.2). broker 운영 위치만 미정 | 설계 §7.4, 역할 §8 |
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
| Common | 4명 공동, 통합 관리 방대혁 | 37.5h | 최종 intake·epoch·fresh bootstrap·Runtime schema·검증기·5화면 전환·배포 |
| A Detection | 신동원 | 28.0h | 재계산·알람·R03·score·격리 평가·화면 1·2 |
| B Knowledge | 강연권 | 22.0h | Neo4j 44/85·RAG 정정·임베딩 검색·화면 4·5 |
| C Agent/HITL | 방대혁 | 42.5h | LangGraph·조치·승인·n8n·Kafka·delivery·화면 3 |
| D Audit·확장 | 천승현 | 15.5h | 감사 read model·화면 3 감사 tab·선택 Text2SQL |
| **합계** | | **145.5h** | P2 도전 과제 제외 |

우선순위별 공수는 **P0 101.5h / P1 44.0h**이며 P2 5.5h는 합계에서 제외한다.
Task 수는 86건(P2 3건 포함)이다.

---

## 3. Common — 최종 intake·bootstrap·통합

### V5-CM-1. source intake와 epoch

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-1.1 | P0 | 최종 ZIP intake. 완료: `project.zip` SHA-256·선별 파일 12종 해시가 기준표와 일치하고, `sample/data`·`schema`·`ontology`·`rag`·`mvp/gen_sample_data.py`만 source artifact로 등록한다. 참고 Backend·Frontend·`node_modules`는 제외한다 | FR-I-04, NFR-06 | — | 1.5h |
| V5-CM-1.2 | P0 | epoch 발급. 완료: `fdc_final_20260818` epoch를 발급하고 `kosa_0813` artifact와 동시 참조를 금지한다. 이전 epoch manifest·marker는 이력 디렉터리로 격리한다 | FR-I-04 | V5-CM-1.1 | 1.0h |
| V5-CM-1.3 | P0 | source manifest v4. 완료: 9개 CSV의 컬럼·행 수·typed content hash와 `03_schema_clean.sql`·`master.cypher`·Generator 해시를 한 manifest로 고정한다. 행 수는 기준표 실측값과 일치한다 | FR-I-04, NFR-06 | V5-CM-1.2 | 1.5h |
| V5-CM-1.4 | P0 | Generator 재현 검증. 완료: `gen_sample_data.py`를 격리 실행해 9개 CSV가 byte-identical로 재생성됨을 확인하고 결과를 manifest provenance에 남긴다 | NFR-06 | V5-CM-1.3 | 1.0h |
| V5-CM-1.5 | P0 | 구 epoch 정리. 완료: v4의 corrected build 파이프라인(`dim_parameter` overlay·`seq_no`·시각 보정)을 최종 epoch 경로에서 제외하고, 폐기 사유를 문서화한다. 코드 삭제 여부는 별도 정리 Task로 둔다 | FR-I-04 | V5-CM-1.2 | 0.5h |
| V5-CM-1.6 | P1 | **v4 corrected build 파이프라인 제거**. 완료: `build_corrected_dataset`·`load_corrected_base`·`load_evaluation_mock`·`corrections/`와 대응 테스트 7개를 삭제하고, `manifest_v3`의 `corrected_files` artifact type·`(runtime\|evaluation, corrected_base)` stage 계약과 `verify_bootstrap_state`의 corrected 경로·marker를 함께 걷어낸다. `data/corrected`와 corrected manifest 2·marker 3도 정리한다. 전체 회귀가 통과해야 한다 | FR-I-04, NFR-06 | V5-CM-2.4 | 2.5h |

### V5-CM-2. fresh bootstrap

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-2.1 | P0 | 재구축 runner 골격. 완료: host·database allowlist, epoch 확인, `--confirm-target`·`--change-ref`, advisory lock, 단일 transaction, `--preflight`/`--rehearse`/apply/`--register-manifests` 모드 배타를 갖춘다. 모드 오류는 sanitized reason과 exit 2로 끝나고 traceback을 내지 않는다 | FR-I-04, NFR-01 | V5-CM-1.3 | 2.0h |
| V5-CM-2.2 | P0 | schema 재생성. 완료: 대상 DB의 `public` schema를 비우고 멘토 `03_schema_clean.sql`을 **원문 그대로** 실행한다. 파일 SHA가 pin과 다르면 DDL 이전에 중단한다. 생성 결과는 base 9 table이며 그 밖의 객체 0건이다 | FR-I-04 | V5-CM-2.1 | 1.5h |
| V5-CM-2.3 | P0 | profile별 적재. 완료: BOM 제거 후 FK 순서대로 적재하고 runtime 2개는 **8개 CSV(action 제외)**, evaluation은 **9개 전부**를 넣는다. 최종 상태는 action 0 / 0 / 12다 | FR-I-04 | V5-CM-2.2 | 1.5h |
| V5-CM-2.4 | P0 | 적재 검증. 완료: 9 table 행 수·typed content hash·PK 중복 0·FK 누락 0을 manifest와 대조하고, `evaluation` IN 4,538/OOC 216/OOS 46과 알람 138·51을 확인한다 | FR-I-04, NFR-06 | V5-CM-2.3 | 1.5h |
| V5-CM-2.5 | P0 | 재실행·복구. 완료: 이미 적재된 DB에 재실행하면 no-op이고, 부분 실패는 단일 transaction rollback으로 되돌아간다. marker 유실은 `--recover-artifact`로만 복구한다 | FR-I-04 | V5-CM-2.4 | 1.0h |

### V5-CM-3. Runtime schema

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-3.1 | P0 | reference extension 재기준화. 완료: R03·document corpus·chunk·`nl_query_log`·`v_alarm_event`를 3개 DB에 생성한다. View는 `h.wafer_id = a.wafer`로 join하고 `wafer_id`·`wafer_no`를 별도 컬럼으로 반환한다. 저장 알람 189·R03 포함 192·AlarmRef 중복 0을 검증한다 | FR-A-06, FR-B-02, FR-I-04 | V5-CM-2.4 | 2.0h |
| V5-CM-3.2 | P0 | Agent Runtime migration. 완료: runtime 2개에만 설계 §3.4의 9 table을 생성한다. `action_history=0` guard, evaluation 적용 거부, legacy FK 0건, 부분 고유 인덱스를 포함한다 | FR-C-04~09 | V5-CM-3.1 | 2.0h |
| V5-CM-3.3 | P0 | action/severity pair guard. 완료: 명명 CHECK로 반쪽 NULL 행을 차단한다. 배포 후 16조합 중 정상 4조합만 수락됨을 실제 INSERT·rollback으로 증명한다 | FR-C-03, FR-C-07 | V5-CM-3.2 | 1.0h |
| V5-CM-3.4 | P0 | Checkpoint 초기화. 완료: runtime 2개에만 `PostgresSaver.setup()`을 one-shot 실행한다. 앱 startup의 `.setup()` 호출 0회, 재실행 시 catalog·migration version·행 수 무변경, thread 재개 smoke를 확인한다 | FR-C-04 | V5-CM-3.3 | 1.5h |
| V5-CM-3.5 | P0 | 최소권한 role. 완료: profile별 app/readonly/logger/delivery 허용·거부 matrix를 적용하고 생성 SQL을 writer 계정으로 실행하지 않는다. `PUBLIC` 권한 0건을 확인한다 | NFR-01, FR-D-03 | V5-CM-3.2 | 1.5h |

### V5-CM-4. 공통 계약과 통합

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-CM-4.1 | P0 | 공통 Enum·DTO. 완료: `AlarmRef`, Action/Approval/Delivery/Run 상태, Fault 5-class, 오류 body를 최종 기준으로 정렬한다. 공개 승인 요청은 `APPROVED\|REJECTED`이고 내부 Enum은 adapter에서만 변환한다 | FR-I-07, NFR-10 | V5-CM-1.2 | 1.5h |
| V5-CM-4.2 | P0 | 감사 쓰기 계약. 완료: event enum·entity mapping·append-only helper와 트랜잭션 규칙을 제공한다. UPDATE·DELETE 경로를 만들지 않는다 | FR-D-07, NFR-05 | V5-CM-4.1 | 1.0h |
| V5-CM-4.3 | P0 | profile 통합 검증기. 완료: 3개 DB의 stage·table·행 수·hash·권한·marker를 한 번에 검사하고 target별 결과를 보존한다. 한 target의 실패가 전체 report를 지우지 않는다 | FR-I-04 | V5-CM-3.5 | 2.0h |
| V5-CM-4.4 | P0 | API contract test. 완료: 호환 필수 9개와 `GET /ontology/graph`의 OpenAPI·응답 구조를 검증하고 배열 응답과 페이지 응답을 같은 path에서 혼용하지 않는다 | FR-I-07 | V5-CM-4.1 | 1.5h |
| V5-CM-4.4-1 | P0 | **5화면 navigation 전환**. 완료: 현재 7개 메뉴(`/dashboard`·`/alarms`·`/traces`·`/agent-runs`·`/actions`·`/analytics`·`/audit-logs`)를 canonical 5영역(Dashboard·Alarm History·Agent·Documents·Ontology)으로 재구성한다. 기존 상세 route는 하위 흐름·deep link로 유지하고 독립된 제6 화면을 만들지 않는다. Text2SQL route는 선택 확장으로 분리한다 | FR-I-02 | V5-CM-4.4 | 2.0h |
| V5-CM-4.4-2 | P0 | **호환 projection·wrapper 연결**. 완료: 호환 필수 9개 API projection과 `GET /ontology/graph` 소비를 연결하고 `api.audit()` wrapper를 Agent 감사 subview에서 실제로 호출한다. 참고 React의 축약 field는 canonical field에서 파생하고 `lot_history.fault_code`를 Agent 예측처럼 직렬화하지 않는다 | FR-I-02, FR-I-03 | V5-CM-4.4-1 | 1.5h |
| V5-CM-4.4-3 | P1 | **alias 제거 조건**. 완료: compatibility projection의 alias 목록과 제거 조건(모든 소비 화면이 canonical field로 전환 완료)을 문서화하고, 조건 충족 시 alias를 삭제하는 후속 Task를 등록한다 | FR-I-03 | V5-CM-4.4-2 | 0.5h |
| V5-CM-4.5 | P1 | compose·배포 통합. 완료: PostgreSQL·Neo4j·Kafka·n8n·Backend·Frontend를 하나의 compose로 올리고 `/api` proxy·CORS origin allowlist·고정 image tag를 적용한다 | FR-I-04, FR-I-06 | V5-CM-4.3 | 1.5h |
| V5-CM-4.6 | P1 | readiness·복구. 완료: `/health/ready`가 PostgreSQL·Neo4j·n8n을 병렬 timeout으로 검사하고 Neo4j 44/85 marker와 ACTIVE corpus revision을 확인한다 | FR-I-05 | V5-CM-4.5 | 1.0h |
| V5-CM-4.7 | P1 | E2E reset guard. 완료: host·DB·token 확인 후 `kosa_agent_e2e`의 실행 데이터만 초기화한다. `kosa_agent`·`kosa_text2sql` 대상은 거부하고 source·reference·corpus·checkpoint schema를 보존한다 | 요구사항 §7.3 | V5-CM-3.4 | 1.5h |

**Common 합계: 35.0h**

---

## 4. A — Detection Full-stack

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-A-1.1 | P0 | Summary 재계산. 완료: Trace에서 `summary_data` 4,800건을 결정론적으로 재현하고 불일치 0건을 확인한다 | FR-A-01 | V5-CM-2.4 | 2.0h |
| V5-A-1.2 | P0 | evaluation 재현. 완료: point 판정으로 IN 4,538 / OOC 216 / OOS 46을 재현한다. `upper_only` parameter의 하한 미판정을 포함한다 | FR-A-02 | V5-A-1.1 | 2.0h |
| V5-A-1.3 | P0 | TRACE·SUMMARY 알람. 완료: TRACE 138·SUMMARY 51을 재현하고 저장 알람 합계 189를 확인한다. 시각 NULL 0건이다 | FR-A-02 | V5-A-1.2 | 2.0h |
| V5-A-1.4 | P0 | R03 파생. 완료: 같은 chamber·parameter·recipe step에서 `chamber_wafer_cum` 오름차순 연속 3 최초 도달로 3건을 발행한다. 각 R03는 member wafer 3개와 TRACE AlarmRef 9개를 갖는다 | FR-A-03 | V5-A-1.3 | 2.0h |
| V5-A-1.5 | P0 | incident 집계. 완료: 알람이 있는 `(lot_id, chamber_id)` 12개를 산출하고 참고 action 12건과 1:1임을 확인한다. R03 포함 합계 192를 검증한다 | FR-A-03, FR-A-06 | V5-A-1.4 | 1.5h |
| V5-A-2.1 | P1 | 비지도 anomaly score. 완료: LOT 단위 분리로 재현 가능한 score를 만들고 feature leakage 0건을 검증한다 | FR-A-04, NFR-08 | V5-A-1.5 | 2.5h |
| V5-A-2.2 | P0 | score 경계 고정. 완료: score가 조치·incident·승인 게이트에 전달되지 않음을 계약 테스트로 고정한다. score 없이도 규칙 처리가 동일하다 | FR-A-05 | V5-A-2.1 | 1.0h |
| V5-A-2.3 | P0 | 합성 라벨 격리. 완료: `fault_code`를 평가 loader에서만 읽고 Runtime repository 타입과 분리한다. 모델 feature·threshold 선택에 사용하지 않음을 테스트로 고정한다 | FR-A-08, NFR-03 | V5-A-2.1 | 1.5h |
| V5-A-2.4 | P1 | Detection 평가 artifact. 완료: metrology 48/600 coverage와 `label_source=SYNTHETIC_GENERATOR`·`production_ground_truth_available=false`를 함께 기록한다. 48건 결과를 600건으로 외삽하지 않는다 | FR-A-09 | V5-A-2.3 | 2.0h |
| V5-A-3.1 | P0 | `GET /alarms`. 완료: 최종 기준 필터와 `(source, alarm_id)` 식별을 제공하고 R03 포함 여부를 명시 파라미터로 구분한다 | FR-A-06 | V5-A-1.5, V5-CM-4.1 | 2.0h |
| V5-A-3.2 | P0 | `GET /trace`·`GET /parameters`. 완료: 참고 React 호환 응답과 canonical field를 동시에 만족하는 projection을 제공한다 | FR-A-06 | V5-A-3.1 | 1.5h |
| V5-A-3.3 | P1 | 화면 1 Dashboard. 완료: KPI·추이·상위 parameter를 실제 API로 연결하고 Loading/Error/Empty/Success를 구분한다 | FR-A-07, FR-I-02 | V5-A-3.2 | 2.5h |
| V5-A-3.4 | P1 | 화면 2 Alarm History. 완료: 목록·필터·상세를 연결하고 source-aware deep link를 제공한다 | FR-A-07, FR-I-02 | V5-A-3.3 | 2.5h |
| V5-A-3.5 | P1 | 호환 필드 adapter. 완료: 참고 React 축약 필드를 한시 지원하고 canonical field로 교체하는 경로를 남긴다 | FR-I-03 | V5-A-3.2 | 1.0h |
| V5-A-4.1 | P1 | Detection 회귀. 완료: 재계산·알람·R03·incident 수치를 fixture로 고정하고 CI에서 재현한다 | NFR-06 | V5-A-2.4 | 2.0h |

**A 합계: 28.0h**

---

## 5. B — Knowledge Full-stack

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-B-1.1 | P0 | graph 독립 검증. 완료: `master.cypher`를 독립 파싱해 44 nodes / 85 relationships·필수 속성·방향·중복 0을 확인한다 | FR-B-01 | V5-CM-1.3 | 1.5h |
| V5-B-1.2 | P0 | 안전 loader. 완료: 선두 `MATCH (n) DETACH DELETE n`을 격리하고 empty/fingerprint/backup/confirm guard를 통과한 경우에만 공용 Neo4j를 갱신한다 | FR-B-01, NFR-01 | V5-B-1.1 | 2.0h |
| V5-B-1.3 | P0 | relation ID. 완료: 방향·type·business endpoint로 stable `relation_id`를 부여하고 elementId를 API provenance로 쓰지 않는다 | FR-B-03 | V5-B-1.2 | 1.0h |
| V5-B-2.1 | P0 | RAG correction overlay. 완료: 원문을 수정하지 않고 overlay로 고정 `EQP01 → EQP04`·score 상향·metrology 기반 조치 상하향·구 10건 서술을 제거한다. PH-9000은 EQP01~03·RECIPE01/03, ET-7500은 EQP04~06·RECIPE02/04로 정정한다 | FR-B-02 | V5-B-1.3 | 2.0h |
| V5-B-2.2 | P0 | **임베딩 확정**. 완료: provider·model·차원을 결정해 `.env`와 corpus revision metadata(`embedding_model_code`·`embedding_dim`)에 기록한다. 선택 근거와 재현 절차를 남긴다 | FR-B-05 | V5-B-2.1 | 1.5h |
| V5-B-2.3 | P0 | corpus 적재. 완료: STAGING으로 document·chunk·embedding을 적재해 문서 수·chunk 수·차원·hash·검색 smoke를 검증한 뒤 ACTIVE로 swap한다. 문서 ID `DOC-SPEC-PH9000`·`DOC-SPEC-ET7500`·`DOC-TROUBLE-FDC`를 승계한다 | FR-B-02, FR-B-05 | V5-B-2.2 | 2.0h |
| V5-B-3.1 | P0 | `POST /documents/search`. 완료: 임베딩 검색 결과에 document/chunk/corpus revision과 실제 근거 내용을 반환한다 | FR-B-04 | V5-B-2.3 | 1.5h |
| V5-B-3.2 | P0 | `GET /ontology/graph`. 완료: read-only graph adapter를 제공하고 Neo4j Browser iframe·Frontend 비밀번호 노출을 제거한다 | FR-B-06, NFR-02 | V5-B-1.3 | 1.5h |
| V5-B-3.3 | P0 | `get_equipment_context` Tool. 완료: chamber·설비·모델·area와 Process Step 인접을 반환하고 고정 설비 upstream을 만들지 않는다 | FR-B-03 | V5-B-3.2 | 1.5h |
| V5-B-3.4 | P0 | `search_documents` Tool. 완료: Agent가 소비할 chunk·corpus revision·score 계약을 고정한다 | FR-B-04 | V5-B-3.1 | 1.0h |
| V5-B-4.1 | P1 | 화면 4 Documents. 완료: 검색·근거 표시를 연결하고 Agent 화면 deep link를 지원한다 | FR-B-06, FR-I-02 | V5-B-3.1 | 2.0h |
| V5-B-4.2 | P1 | 화면 5 Ontology. 완료: graph API 기반 시각화를 제공하고 비밀정보를 노출하지 않는다 | FR-B-06, NFR-02 | V5-B-3.2 | 2.5h |
| V5-B-4.3 | P1 | 검색 평가. 완료: 독립 graph·문서 fixture로 Recall@K·MRR·실패 사례와 corpus revision을 artifact에 기록한다 | FR-B-07 | V5-B-3.4 | 2.0h |
| V5-B-4.4 | P2 | 하이브리드 검색. 완료: 키워드+벡터 결합 실험과 비교표 | FR-B-08 | V5-B-4.3 | 2.0h |

**B 합계: 22.0h** (P2 2.0h 제외)

---

## 6. C — Agent·HITL·n8n·Kafka Full-stack

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-C-0.1 | P0 | Runtime Repository. 완료: 설계 §3.4의 9 table에 대응하는 Repository와 ID·감사 계약을 만든다. `action`·`severity`는 항상 함께 채운다 | FR-C-07, NFR-05 | V5-CM-3.3 | 2.0h |
| V5-C-0.2 | P0 | thread·checkpoint 계약. 완료: `agent_run_id`와 독립인 thread UUID, 저장·interrupt·동일 thread 재개 fixture를 만든다 | FR-C-04 | V5-CM-3.4, V5-C-0.1 | 1.5h |
| V5-C-1.1 | P0 | incident 해석. 완료: source-aware `AlarmRef`를 `(lot_id, chamber_id)`로 묶고 대표 알람을 `occurred_at ASC, source priority, alarm_id ASC`로 결정한다 | FR-C-01 | V5-A-1.5, V5-C-0.1 | 2.0h |
| V5-C-1.2 | P0 | 실제 routing 결합. 완료: `lot_history`로 LOT/WAFER routing을 조회하고 B의 Process Step 인접과 결합한다. 불일치는 `route_consistency=false`로 보존하고 합성하지 않는다 | FR-C-10 | V5-C-1.1, V5-B-3.3 | 2.0h |
| V5-C-1.3 | P0 | 중복 실행 방지. 완료: 동일 incident 동시 요청에서 활성 run 1개만 만들고 처리 완료 incident는 재선택하지 않는다 | FR-C-09, FR-C-14 | V5-C-1.1 | 1.5h |
| V5-C-2.1 | P0 | LangGraph 골격. 완료: load_incident → Tool 수집 → 가설 → 조치 → 저장 → delivery/HITL → finalize 그래프를 구성한다 | FR-C-02 | V5-C-1.2 | 2.5h |
| V5-C-2.2 | P0 | Tool 예산. 완료: 총 8회·동일 Tool 재시도 상한·전송 예약을 HITL 중단·재개 전후 누적 적용하고 DB에서 복원한다 | FR-C-08 | V5-C-2.1 | 2.0h |
| V5-C-2.3 | P0 | 원인 가설. 완료: `FOC|RFM|MFD|TMD|OTH` 구조화 출력과 근거 인용을 생성한다. `NRM`을 출력하지 않고 합성 라벨·Generator FAULTS를 프롬프트에 넣지 않는다 | FR-C-13, NFR-03 | V5-C-2.2 | 2.5h |
| V5-C-3.1 | P0 | `decide_action`. 완료: SUMMARY OOC-only → MONITORING, TRACE OOS → WARNING, strict R03 → EQP_HOLD의 3단계 순수 규칙 함수를 만든다. LLM·score·metrology를 입력에서 제외한다 | FR-C-03 | V5-C-2.3 | 1.5h |
| V5-C-3.2 | P0 | action 생성 transaction. 완료: `action_history`·link·approval·delivery를 한 트랜잭션에서 만들고 incident당 유효 action 1건을 보장한다 | FR-C-14 | V5-C-3.1 | 2.0h |
| V5-C-3.3 | P0 | HITL 승인. 완료: EQP_HOLD에서 그래프를 중단하고 승인·반려 후 동일 thread를 재개한다. 조건부 갱신으로 중복 결정을 409로 막는다 | FR-C-04, FR-C-05 | V5-C-3.2, V5-C-0.2 | 2.5h |
| V5-C-4.1 | P0 | **n8n workflow 제작**. 완료: `WF1-alarm-to-agent`·`WF2-notify-email`·`WF3-mes-hold`·`WF4-result-writeback`을 만들고 JSON을 `deploy/n8n/`에 커밋한다 | FR-C-12 | V5-C-3.3 | 2.5h |
| V5-C-4.2 | P0 | **compose에 n8n 추가**. 완료: 팀 compose에 n8n 서비스(5678·볼륨·basic auth)를 정의하고 Backend와 같은 네트워크에 둔다 | FR-C-12, FR-I-04 | V5-C-4.1 | 1.0h |
| V5-C-4.3 | P0 | SMTP delivery. 완료: WARNING 이메일 1회, EQP_HOLD 승인요청 이메일 1회를 서명 webhook으로 발송하고 실패·timeout을 기록한다 | FR-C-06, FR-C-12 | V5-C-4.2 | 2.0h |
| V5-C-4.4 | P0 | write-back callback. 완료: `POST /internal/actions/{action_id}/delivery`가 timestamp·HMAC 서명·300초 replay window를 검증하고 channel별 상태를 갱신한다 | FR-C-06 | V5-C-4.3 | 1.5h |
| V5-C-4.5 | P0 | Kafka MES Mock. 완료: 승인된 EQP_HOLD만 n8n Kafka Producer로 `fdc.actions`에 발행하고, MES Mock consumer 결과를 `fdc.actions.result` → write-back으로 반영한다. 승인 전 발행 0건·반려 시 발행 0건을 음성 테스트로 고정한다 | FR-C-06, FR-C-12 | V5-C-4.4 | 2.0h |
| V5-C-4.6 | P0 | 채널 멱등성. 완료: `(action_id, channel)`별 외부 효과 최대 1회, 응답 유실은 `UNKNOWN` 전이·자동 재발송 0회를 보장한다 | FR-C-06 | V5-C-4.4 | 1.5h |
| V5-C-5.1 | P0 | 필수 API 4종. 완료: `GET /agent/runs`, `POST /agent/ask`, `GET /approvals`, `POST /approvals/{approval_id}/decision`을 제공한다. 공개 승인 body는 `APPROVED\|REJECTED`다 | FR-C-05, FR-I-07 | V5-C-3.3 | 2.0h |
| V5-C-5.2 | P1 | 화면 3 Agent. 완료: 실행·승인·action·delivery 상태와 근거 deep link를 연결하고 `api.audit()`를 감사 subview에 연결한다 | FR-C-13, FR-I-02 | V5-C-5.1, V5-D-1.2 | 3.0h |
| V5-C-6.1 | P0 | golden flow E2E. 완료: incident 12개 기준으로 MONITORING 5 / WARNING 4 / EQP_HOLD 3 흐름과 중복 실행·동시 승인·재전송·복구를 검증한다 | FR-C-09, NFR-04 | V5-C-4.6 | 2.5h |
| V5-C-6.2 | P1 | Fault 5-class 평가. 완료: 단일 distinct non-NRM 라벨 TRACE incident 7건에만 적용하고 SUMMARY-only 5건은 `NO_INJECTED_FAULT`, 혼합 라벨은 `AMBIGUOUS_LABEL`로 제외·보고한다. 임의 OTH·다수결 정답을 만들지 않는다 | FR-C-15, NFR-03 | V5-C-6.1, V5-A-2.3 | 2.5h |
| V5-C-7.1 | P2 | Level 3 ReAct 비교 | FR-C-11 | V5-C-6.2 | 2.0h |

**C 합계: 42.5h** (P2 2.0h 제외)

---

## 7. D — 감사·선택 확장 Analytics

| ID | P | 완료 기준 | 요구사항 | 선행 | 공수 |
|---|---|---|---|---|---:|
| V5-D-1.1 | P0 | 감사 read model. 완료: `audit_log`를 직접 조회하고 `action_history`에서 사후 합성하지 않는다. `occurred_at DESC, audit_id DESC` 안정 정렬을 적용한다 | FR-D-07, NFR-05 | V5-CM-4.2, V5-CM-3.2 | 1.5h |
| V5-D-1.2 | P0 | `GET /audit-logs`. 완료: event·actor·entity·기간 필터와 동일 필터 전체 집계를 제공하고 UPDATE·DELETE 경로를 만들지 않는다 | FR-D-07 | V5-D-1.1 | 1.5h |
| V5-D-1.3 | P1 | 화면 3 감사 subview. 완료: 필터·정렬·상세를 연결하고 `api.audit()` wrapper를 실제로 소비한다 | FR-D-07, FR-I-02 | V5-D-1.2 | 2.0h |
| V5-D-2.1 | P1 | schema allowlist·pool. 완료: 최종 schema 기준 table/column allowlist와 runtime readonly·evaluation readonly pool을 분리한다. DSN fallback 0건 | FR-D-03, NFR-01 | V5-CM-3.5 | 2.0h |
| V5-D-2.2 | P1 | SQL 안전 검증 **(선택 확장)**. 완료: 단일 SELECT·AST 방어·위험 함수 차단·다중 문장 차단·LIMIT 500을 적용하고 방어 fixture가 전부 미실행이다 | FR-D-02, NFR-07 | V5-D-2.1 | 2.5h |
| V5-D-2.3 | P1 | 분석 계획·실행 **(선택 확장)**. 완료: 자연어를 SQL·metric·group_by·visualization 계획으로 변환하고 table·bar·line·histogram 결과를 반환한다 | FR-D-01, FR-D-04 | V5-D-2.2 | 2.5h |
| V5-D-2.4 | P1 | 질의 이력 **(선택 확장)**. 완료: 성공·정책 거부·실행 오류를 최소권한 writer로 기록하고 log pool은 SQL 실행 권한을 갖지 않는다 | FR-D-05 | V5-D-2.3 | 1.5h |
| V5-D-2.5 | P1 | 선택 확장 UI·평가. 완료: 5화면 navigation과 분리된 route로 제공하고 미구현·장애 시에도 기본 5화면이 정상 동작한다. 질문셋 12건 이상 중 10건 이상 정답을 기록한다 | FR-D-06, FR-D-08 | V5-D-2.4 | 2.0h |
| V5-D-3.1 | P2 | MCP 서버 노출 | FR-D-10 | V5-D-2.5 | 1.5h |

**D 합계: 15.5h** (P2 1.5h 제외)

---

## 8. 적용 순서와 게이트

```text
1  V5-CM-1.*        source intake · epoch · manifest
2  V5-CM-2.*        fresh bootstrap (e2e → agent → text2sql 순서)
3  V5-CM-3.1~3.3    reference extension · Runtime migration · pair guard
4  V5-CM-3.4~3.5    checkpoint · 최소권한 role
5  V5-CM-4.3        통합 검증기 전체 PASS
6  A·B·D 병렬 착수   재계산·graph/RAG·감사
7  C 착수            Runtime Repository → LangGraph → 조치 → HITL → n8n
8  V5-CM-4.4-1~3    5화면 navigation 전환 · 호환 projection · alias 제거 조건
9  V5-CM-4.5~4.7    compose·readiness·E2E reset
10 통합 E2E·평가 artifact·최종 검증
```

**착수 게이트는 `V5-CM-2.4`(적재 검증) 통과다.** 여기서 데이터가 확정되고 이후 재적재가
없으므로 그 시점부터 팀원이 붙어도 안전하다. 실제 해금은 B가 `V5-CM-1.3` 직후(누적 4.0h),
A가 `V5-CM-2.4` 직후(10.5h), C·D가 `V5-CM-3.2~3.3` 직후(15.5h)다. `V5-CM-4.3`은
`V5-CM-4.5`만 선행하므로 팀원 착수를 막지 않는다.

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

## 10. 미결 사항

| 항목 | 상태 | 결정 시점 |
|---|---|---|
| 임베딩 provider·model·차원 | B가 `V5-B-2.2`에서 확정 | 구현 단계 |
| Kafka broker 운영 위치(공용 서버 1벌 / 팀원 로컬) | 구현은 필수로 진행. 배치만 미정 | `V5-CM-4.5` compose 통합 시 |
| Text2SQL 활성 여부 | 선택 확장. 필수 인수 기준에 미포함 | 일정 여유에 따라 |
| ~~v4 corrected build 코드 삭제~~ | **`V5-CM-1.6`으로 등록 완료** | `V5-CM-2.4` 이후 |
| compatibility alias 제거 | `V5-CM-4.4-3`이 조건만 등록. 삭제는 후속 Task | 모든 화면 canonical 전환 후 |
