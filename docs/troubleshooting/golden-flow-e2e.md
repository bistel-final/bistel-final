# Golden flow E2E 실행 가이드

> Task: `V5-C-6.1` · dataset epoch: `fdc_final_20260818`
>
> 이 문서는 구현과 공용 실행을 분리한다. 코드·격리 테스트가 끝나도 아래 공용 Gate
> 전제와 사용자 승인이 없으면 `kosa_agent_e2e` reset·batch·승인·전송을 실행하지 않는다.

## 1. 현재 단계와 공용 실행 전제

1단 구현은 read-only verifier, 독립 12건 oracle, 격리 PostgreSQL 회귀까지다. 공용
PostgreSQL·n8n·Kafka·SMTP에는 접근하지 않는다.

2단 공용 실행은 다음을 모두 충족한 뒤 별도 승인으로 진행한다.

- `r03_alarm_history` exact 3건과 12 incident dry-run
- 공용 n8n 2.32.7의 WF2~WF4 활성 및 credential 확인
- Kafka `fdc.actions` consumer·offset 확인
- 사용자 명시 승인과 `V5-CM-4.7` reset confirmation
- reset 전후 `kosa`, `kosa_agent` 불변 manifest

## 2. 고정 oracle

정본은 `backend/tests/fixtures/v5_c_6_1/golden_incidents.json`이다. 최종 source ZIP의
`trace_alarm_history.csv`·`summary_alarm_history.csv` 합집합을 canonical
`(lot_id, chamber_id)`로 묶고, `evaluation.csv`와 `lot_history.csv`에
`R03_CONSEC_V1`을 적용해 R03 3건을 독립 재계산했다. `action_history`와 Runtime 실행
결과는 oracle 입력으로 쓰지 않는다.

- incident 12
- `MONITORING` 5 / `WARNING` 4 / `EQP_HOLD` 3
- R03 포함 incident 3
- fixture의 `source_manifest_sha256`은 현재
  `infra/bootstrap/source-manifest-v4.json` 파일 SHA-256과 실행 때 다시 대조한다.

## 3. phase와 실행 범위

| 순서 | phase | public Gate scope | 최소 artifact |
|---:|---|---|---|
| 1 | `PREFLIGHT` | `PUBLIC_E2E` | dry-run `BATCH_NDJSON`, `DB_SNAPSHOT` |
| 2 | `BATCH_BASELINE` | `PUBLIC_E2E` | once `BATCH_NDJSON`, wall-clock `HTTP_RESULTS`, `DB_SNAPSHOT` |
| 3 | `PRE_APPROVAL` | `PUBLIC_E2E` | `N8N_EXECUTIONS`, `KAFKA_OFFSETS`, `SMTP_RECEIPT`, `DB_SNAPSHOT` |
| 4 | `DECISIONS` | `PUBLIC_E2E` | `HTTP_RESULTS`, `N8N_EXECUTIONS`, `KAFKA_OFFSETS`, `DB_SNAPSHOT` |
| 5 | `UNKNOWN` | `PUBLIC_E2E` | `HTTP_RESULTS`, `KAFKA_OFFSETS`, `DB_SNAPSHOT` |
| 6 | `MANUAL_RETRY` | `ISOLATED_CONTAINER` 허용 | `DB_SNAPSHOT` |
| 7 | `SECOND_BATCH` | `PUBLIC_E2E` | 직전 dry-run과 once 각 `BATCH_NDJSON`, wall-clock `HTTP_RESULTS`, `DB_SNAPSHOT` |

`LEVEL_COMPARISON`은 Level 1·2 모두 `ISOLATED_CONTAINER`이고 `level_round`는
`[1,2]`다. 각 phase·round마다 위 artifact를 각각 둔다. public SMTP·Kafka 효과는
발생시키지 않는다.

## 4. DB snapshot 수집

각 phase가 끝난 즉시, 다음 phase로 넘어가기 전에 같은 `kosa_app` role로
`read_golden_flow_snapshot()` 결과를 JSON으로 저장한다. 함수는 다음 순서를 강제한다.

1. `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`
2. 첫 SELECT로 `current_database()='kosa_agent_e2e'`, `current_user='kosa_app'`
3. run·action·approval·delivery·tool·audit·R03·재수화 크기를 한 business SELECT로 읽기

JSON은 `GoldenFlowSnapshot`을 `dataclasses.asdict()`한 뒤 `r03_incidents`의 각
`(lot_id, chamber_id)` tuple을 `{"lot_id":...,"chamber_id":...}` 객체로 바꾼 값이다.
필수 top-level key는 `runs`, `actions`, `approvals`, `deliveries`, `tools`, `audits`,
`r03_incidents`이며 여분·누락 key를 허용하지 않는다. phase가 끝난 뒤 DB가 바뀌었는데
snapshot을 다시 만들면 과거 전이를 증명하지 못하므로 해당 round는 reset 후 재수행한다.

## 5. 외부 artifact JSON 계약

모든 JSON은 UTF-8이며, 아래 예시의 key는 exact다.

`KAFKA_OFFSETS`:

```json
{"format_version":1,"topic":"fdc.actions","before":100,"after":100}
```

`N8N_EXECUTIONS`:

```json
{"format_version":1,"executions":[{"workflow":"WF2","action_id":"ACT-...","status":"SUCCESS","execution_id":"sanitized-id"}]}
```

`SMTP_RECEIPT`:

```json
{"format_version":1,"receipts":[{"action_id":"ACT-...","status":"SENT","receipt_id":"sanitized-id"}]}
```

`HTTP_RESULTS`는 case별 결과와 batch monotonic 시간을 함께 담을 수 있다.

```json
{"format_version":1,"results":[
  {"case":"APPROVE","status_code":200,"action_id":"ACT-..."},
  {"case":"CONCURRENT_DECISION","status_code":200,"action_id":"ACT-..."},
  {"case":"CONCURRENT_DECISION","status_code":409,"action_id":"ACT-..."},
  {"case":"UNKNOWN_RETRY","exit_code":3,"action_id":"ACT-...","before_status":"UNKNOWN","after_status":"UNKNOWN"},
  {"case":"FAILED_RETRY","exit_code":0,"action_id":"ACT-..."},
  {"case":"BATCH_WALL_CLOCK","duration_ms":1234}
]}
```

자격증명, 전체 DSN, SMTP 주소, n8n payload 원문은 artifact에 넣지 않는다.

## 6. evidence manifest

manifest top-level은 다음 exact 6개 key다.

```json
{
  "format_version": 1,
  "dataset_epoch": "fdc_final_20260818",
  "gate_kind": "PUBLIC_GOLDEN_FLOW",
  "level_round": 2,
  "phases": {
    "PREFLIGHT": {
      "execution_scope": "PUBLIC_E2E",
      "artifact_ids": ["preflight-batch", "preflight-db"]
    }
  },
  "artifacts": [
    {
      "artifact_id": "preflight-batch",
      "kind": "BATCH_NDJSON",
      "relative_path": "artifacts/preflight.ndjson",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "phase": "PREFLIGHT",
      "level_round": 2,
      "media_type": "application/x-ndjson"
    },
    {
      "artifact_id": "preflight-db",
      "kind": "DB_SNAPSHOT",
      "relative_path": "artifacts/preflight-db.json",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "phase": "PREFLIGHT",
      "level_round": 2,
      "media_type": "application/json"
    }
  ]
}
```

- `artifact_id`와 normalized `relative_path`는 전역 unique다. kind는 반복 가능하다.
- 절대경로, `..`, symlink, 디렉터리는 거부한다.
- artifact SHA-256을 먼저 대조한 뒤 kind parser를 실행한다.
- manifest schema·scope·path·hash·parser는 DB engine을 만들기 전에 모두 검증한다.
- public Gate의 phase 1~5·7은 `PUBLIC_E2E`, phase 6은
  `ISOLATED_CONTAINER`다. Level 비교는 전 phase `ISOLATED_CONTAINER`다.

## 7. verifier 실행

backend 환경의 `POSTGRES_DB`는 반드시 `kosa_agent_e2e`, 접속 role은 `kosa_app`이어야
한다. fallback은 없다.

```bash
cd backend
../.venv/bin/python scripts/verify_golden_flow.py \
  --database kosa_agent_e2e \
  --evidence-file /absolute/path/to/evidence.json
```

단계별 확인은 `--phase PREFLIGHT`처럼 실행한다. 이 모드는 성공해도
`PHASE_PASS`만 출력하며 `GOLDEN_FLOW_PASS`를 출력하지 않는다.

| exit | 의미 |
|---:|---|
| 0 | `PHASE_PASS` 또는 전체 7단 `GOLDEN_FLOW_PASS` |
| 1 | 유효한 증빙의 기대값 불일치 또는 snapshot 조회 실패 |
| 2 | CLI usage 오류 |
| 3 | `TARGET_MISMATCH`, `EVIDENCE_INVALID`, `EVIDENCE_INCOMPLETE` |

## 8. Level 지표 판독

완료율의 분모는 round당 12개 run으로 고정한다. `completion_rate.numerator`는 기존 비교를
위해 `COMPLETED+FAILED` 합을 유지하되, 같은 객체의 `numerator_by_status.COMPLETED`와
`numerator_by_status.FAILED`를 반드시 함께 기록한다. 따라서 실패 증가가 완료율 개선처럼
보이지 않도록 status별 분자를 따로 비교한다. `WAITING_APPROVAL`은 완료 분자에 포함하지 않는다.

## 9. UNKNOWN과 retry 주의

- `UNKNOWN`은 reconciliation 종착 상태다. 자동 재발송하지 않는다.
- `retry`가 exit 3이고 전후 상태가 모두 `UNKNOWN`인지 증명한다.
- 명시 retry 성공은 별도 `FAILED` delivery에서만 확인한다.
- 수동 run retry는 격리 DB에서 원 `FAILED` run을 보존하고 새 run의
  `retry_of_run_id`로 연결한다.

## 10. 종료 판정

전체 PASS는 7개 phase와 필요한 Level round가 모두 존재하고, phase별 DB snapshot과
외부 artifact가 모두 유효할 때만 가능하다. DB 판정이 맞더라도 artifact가 빠지면
`EVIDENCE_INCOMPLETE`이며, 2차 batch는 DB count가 아니라 dry-run empty와 once final
7개 0을 모두 직접 검증한다.
