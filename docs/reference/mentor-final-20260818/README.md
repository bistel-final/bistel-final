# 멘토님 제공 최종 패키지 기준표

> 기준 수신일: 2026-08-18
> 검증일: 2026-08-19
> 원본 파일: `project.zip`
> 원본 SHA-256: `e5ce2c551613e37d49d45afaec9563e17105d69b436ec22e660b302abb5dabe3`
> canonical dataset epoch: `fdc_final_20260818`

이 문서는 최종 패키지의 물리 데이터와 동봉 문서가 서로 다를 때 사용할 저장소 기준표다.
ZIP 전체는 참고 프로젝트·`node_modules`·캐시를 포함하므로 Git이나 Bootstrap 입력으로 직접
사용하지 않는다. 후속 intake Task에서 `sample/data`, DDL, ontology, RAG, Generator만 선별해
별도 source artifact와 manifest로 등록한다.

## 1. 적용 우선순위

최종 패키지 내부에서도 Markdown 가이드 일부가 이전 생성 결과를 적고 있다. 수치·키·스키마가
충돌하면 다음 순서로 판단한다.

1. `sample/data/*.csv`와 `sample/schema/03_schema_clean.sql`
2. 독립 실행 후 CSV를 byte-identical하게 재생성하는 `mvp/gen_sample_data.py`
3. 실제 CSV를 사용하는 참고 Backend·Frontend 및 `docs/검토질문_답변.html`
4. CSV·Generator와 실제로 일치하는 개별 설명
5. 그 밖의 패키지 README·Markdown·배포·개발 가이드

서술 문서의 고정값은 언제나 1~3순위 물리 근거에 종속된다. 특히
`docs/04_알람_재현_가이드.md`, `05_검토질문_답변.md`, `개발자_통합가이드.md`의
TRACE 126, SUMMARY 47, evaluation OOS 42, action 10은 최종 CSV와 다르다. ZIP 루트 README의
6화면 문구와 Generator 주석의 AREA당 설비 2대도 각각 실제 5화면·AREA당 3대와 다르다. 이들
문구는 구현·수용 기준으로 복사하지 않는다.

## 2. 검증된 물리 데이터

| 테이블 | 행 수 | 비고 |
|---|---:|---|
| `dim_parameter` | 8 | 5선과 `upper_only` 포함 |
| `lot_history` | 600 | `fault_code` 공개 합성 라벨 포함 |
| `fdc_trace` | 14,400 | `seq_no=0..5`, 선언 PK 중복 0 |
| `summary_data` | 4,800 | Trace 재계산 불일치 0 |
| `evaluation` | 4,800 | IN 4,538 / OOC 216 / OOS 46 |
| `trace_alarm_history` | 138 | OOS raw point와 일치 |
| `summary_alarm_history` | 51 | 동적 CL±3σ 결과와 일치, 시각 NULL 0 |
| `metrology` | 48 | PASS 39 / FAIL 9, 시각 NULL 0 |
| `action_history` | 12 | 참고 fixture, MONITORING 5 / WARNING 4 / EQP_HOLD 3 |

검증 결과는 다음과 같다.

- 선언 PK 중복 0건, 핵심 FK 누락 0건
- Summary·evaluation·TRACE·SUMMARY 알람 독립 재계산 불일치 0건
- 실제 알람이 있는 `(lot_id, chamber_id)` incident 12개와 action fixture 12건이 1:1
- strict R03 파생 incident 3개
- 저장 알람 기본 합계 189건(TRACE 138 + SUMMARY 51)
- R03를 명시적으로 포함한 합계 192건
- Generator 재실행 결과 9개 CSV byte-identical
- `master.cypher`는 내용이 재현되며 원본 CRLF와 재생성 LF만 다름

`action_history.trigger_alarm_lot_hist_id`와 `recipe_step_name`은 12행 모두 비어 있다. 이 파일은
incident·조치 분포의 평가·화면 참고 fixture로만 사용하고 단일 알람 FK나 Runtime 실행 이력으로
해석하지 않는다. 관계가 필요하면 실제 TRACE·SUMMARY·R03를 `(lot_id, chamber_id)`로 다시
resolve한다.

## 3. Fault 라벨과 평가 경계

| raw label | 행 수 |
|---|---:|
| `NRM` | 554 |
| `FOC` | 15 |
| `RFM` | 12 |
| `MFD` | 13 |
| `TMD` | 2 |
| `OTH` | 4 |

최종 패키지는 Generator가 주입한 공개 합성 Fault 라벨을 제공한다. 다음 메타데이터를 구분한다.

```text
public_fault_ground_truth_available=true
label_source=SYNTHETIC_GENERATOR
production_ground_truth_available=false
usage_scope=EVALUATION_ONLY
```

- 라벨은 격리된 평가에서만 사용한다.
- 모델 feature·threshold 선택의 누수 입력, Agent 프롬프트·Tool·RAG·Runtime 판단 입력으로 전달하지 않는다.
- `NRM`은 raw 정상 라벨이다. 알람 incident를 분류하는 Agent 출력 도메인은
  `FOC|RFM|MFD|TMD|OTH`이며 `NRM`을 고장 가설로 출력하지 않는다.
- `metrology.alarm_result`는 48개 표본의 Detection PASS/FAIL 평가 라벨이며 Fault Mode 라벨이 아니다.
- metrology 48행은 lot_history 600행의 일부 표본이다. 39/9 결과를 전체 600행 Detection 성능으로
  외삽하지 않고 평가 coverage 48/600을 함께 표시한다.
- 합성 데이터 결과를 실제 생산 공정 성능으로 표현하지 않는다.

## 4. 조치와 anomaly score

조치는 다음 deterministic 규칙으로만 결정한다.

| incident 근거 | 조치 | 승인 | 외부 효과 |
|---|---|---|---|
| SUMMARY OOC만 존재 | `MONITORING` | 자동 | 없음 |
| TRACE OOS 존재, strict R03 없음 | `WARNING` | 자동 | n8n SMTP 이메일 |
| strict R03 존재 | `EQP_HOLD` | 사람 승인 | 승인 요청 이메일, 승인 후 Kafka MES Mock |

R03는 같은 `(chamber, parameter, recipe step)`에서 `chamber_wafer_cum` 오름차순으로 LOT
경계를 넘어 계산한다. 비OOS에서 연속 수를 초기화하고 연속 3에 처음 도달할 때 한 번 발행한다.
각 R03는 연속 OOS WAFER를 나타내는 `member_wafer_refs` 3개와, 그 세 WAFER의 raw OOS point에
해당하는 TRACE `member_alarm_refs`를 별도로 가진다. 최종 epoch의 각 R03에는 TRACE AlarmRef가
9개다. WAFER 3개를 AlarmRef 3개로 축약하지 않는다.

`anomaly_score`는 Agent 설명과 화면의 보조 근거다. 조치 상향·하향, incident 생성,
`EQP_HOLD`, 승인 게이트에 사용하지 않는다. score가 없거나 모델이 준비되지 않아도 규칙 처리에는
영향이 없다.

## 5. 외부 연동

- 이메일: n8n을 통한 실제 SMTP 발송
- MES: 실제 팹 연동이 아닌 Kafka `fdc.actions` 목업
- EQP_HOLD 승인 전 MES 이벤트 발행 금지
- 목업 소비 결과: `fdc.actions.result` → n8n write-back
- REST MES Mock은 Kafka를 사용할 수 없을 때의 대안이며 주 계약과 혼용하지 않는다.

## 6. 화면·API 기준

최종 패키지의 실제 React source와 참고 Backend를 기준으로 canonical 화면은 다음 5개다.

1. Dashboard
2. Alarm History
3. Agent
4. Documents
5. Ontology

참고 React는 router가 아니라 `dash|alarm|agent|docs|onto` tab state를 사용한다. `/`, `/alarms`,
`/agent`, `/documents`, `/ontology`는 이 5개 영역을 실제 서비스 URL로 옮기는 팀 설계 계약이며
패키지 snapshot에 이미 구현된 route라고 해석하지 않는다.

Text2SQL·Analytics는 최종 5화면에서 제거되었으므로 필수 범위가 아니라 선택 확장이다. 최종 참고
Frontend의 `api.js`가 노출하는 호환 wrapper 9개는 다음과 같다. 이 중 `api.audit()`는 현재 페이지
소비가 없으므로 팀 구현에서 Agent 감사 subview에 연결해야 한다.

```text
GET  /alarms
GET  /trace
GET  /parameters
GET  /agent/runs
POST /agent/ask
GET  /approvals
POST /approvals/{approval_id}/decision
GET  /audit-logs
POST /documents/search
```

`POST /internal/actions/{action_id}/delivery`는 n8n write-back용 내부 계약이다. Ontology 화면의
참고 구현은 Neo4j Browser를 iframe으로 열지만, 팀 구현에서는 비밀번호를 Frontend에 노출하지
않고 canonical read-only Backend adapter로 대체한다. adapter의 형태는 팀 설계이며
`GET /relations/chambers/{chamber_id}`(chamber 기준 관계 조회)로 확정했다 — 참고 Frontend의
Ontology 화면은 Neo4j Browser iframe 14줄이라 화면 참고 구현이 존재하지 않기 때문이다.
지켜야 할 제약은 **자격증명 미노출 · read-only adapter 경유**(NFR-02) 하나다.

## 7. Neo4j·RAG 주의

- 최종 `master.cypher`의 기대값은 44 nodes / 85 relationships다.
- 첫 문장 `MATCH (n) DETACH DELETE n`을 공용 Neo4j에 직접 실행하지 않는다.
- 기존 destructive-safe loader의 empty/fingerprint/backup/confirm guard를 유지한다.
- Graph에는 고정 설비 간 `UPSTREAM_OF`가 없다. Process Step 인접 관계와 PostgreSQL
  `lot_history` 실제 routing을 결합한다.
- `sample/rag/SPEC_PH-9000_PhotoScanner.md`의 고정 `EQP01 → EQP04` 문장은 위 원칙과
  충돌하므로 정정한 뒤 임베딩한다. 정정본은 별도 경로에 두고 **원본은 `V5-CM-1.1` 등록
  해시 그대로 보존**한다(overlay 구조는 쓰지 않는다 · `V5-B-1.2`).
- TROUBLE 원문의 metrology FAIL·반복·하류 진행 기반 조치 상향과 원인 설명·후속 정상 기반
  하향은 3단계 deterministic rule과 충돌하므로 제거한다. R01은 raw 한 점의 USL/LSL 이탈 즉시
  TRACE 알람으로 통일하고 Fault 후보에 `OTH`를 포함한다.
- PH-9000 적용 범위는 EQP01~03·RECIPE01/03, ET-7500은 EQP04~06·RECIPE02/04이며 문서 본문을
  이 범위로 정정한 뒤 chunking한다.
- 원문 YAML의 문서 ID는 `DOC-SPEC-PH9000`, `DOC-SPEC-ET7500`,
  `DOC-TROUBLE-FDC`이며 정정본에서도 안정적으로 승계한다.
- `document`·`document_chunk` 스키마와 `load_documents.py`·임베딩 모델은 최종 패키지에
  없다. 교육생 배포패키지(①)에서 가져온다 — `docs/reference/배포패키지_기준.md`.

## 8. 선택 artifact 해시

| artifact | SHA-256 |
|---|---|
| `sample/schema/03_schema_clean.sql` | `4a437efc6d853d911c5f82613b4756fafa6368fd144d6cedfb4f81908af8ca8c` |
| `sample/ontology/master.cypher` | `51604707c9a0f3bc97b21773b7bd43d0049f2dacf322042c36f090ec63c74eea` |
| `sample/data/dim_parameter.csv` | `977f4c95bd63750a025cd44dbb8ea08897eb523225894e92f65d668f593041ea` |
| `sample/data/lot_history.csv` | `d0e2d84cd2b268278873bb963cd67445b5f80434a5d71fe8c3926e4896c13118` |
| `sample/data/fdc_trace.csv` | `9840c86f459f4da83aca42cf2f5938d36ef4fc843e40f288097f760cab435545` |
| `sample/data/summary_data.csv` | `1b30af260cb66fd79c43b4777b59dedaf2be58b8f3249d1a15a4e83e153d0c66` |
| `sample/data/evaluation.csv` | `d6495071d18179fff811e995d6d1fc9e683d8bb2f7f030a06993ca1dba2aa9e7` |
| `sample/data/trace_alarm_history.csv` | `aaa43f9e6af5d45d3cdc4c813f0a07691a426130a09dfdf93a3c2fe9edac6686` |
| `sample/data/summary_alarm_history.csv` | `cf16301cb5f03f0213fdb816f4ad15b935c0c6c7e6ed6ef20f63eb30c8121d88` |
| `sample/data/metrology.csv` | `b6d88cd5fb07f8e69189e2e19ff84beb2279cc238238d7e726b547cff3597be2` |
| `sample/data/action_history.csv` | `174e8fd71fab0e716e3d8585057e997d17dc03bb9fbedec957df3a146ca213a1` |
| `mvp/gen_sample_data.py` | `e42e66c84f3c12357f126132f81451ef7e0e8a88e5fc4f080db664331670a24d` |

이 표는 intake 검증 근거다. 실제 Bootstrap manifest는 별도 Task에서 source member 경로·컬럼·
canonical content hash와 profile별 기대 상태를 포함해 재생성한다.

## 9. 전환 상태

| 영역 | 상태 |
|---|---|
| 최종 원본 사실·해시 검증 | 완료 |
| 요구사항·설계·역할·API 재기준화 | 완료 (v2.1·v10.1·API v3) |
| WBS v5 작성 | 완료 |
| source/corrected/profile manifest 재생성 | 미착수 |
| 격리 DB 적재·검증 | 미착수 |
| 공용 DB 전환 | 미착수 |

상위 문서와 WBS v5는 확정됐다. 최종 ZIP의 공용 PostgreSQL·Neo4j 적용은 `V5-CM-2.*`의
preflight → rehearse → apply 절차로만 수행하며, 그 전까지 직접 실행하지 않는다.
