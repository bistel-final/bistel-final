# Incident 일회성 batch 관리 명령

`V5-C-5.3`의 `run_pending_incidents.py`는 Runtime 이력이 전혀 없는 incident만 한 번씩
실행한다. scheduler·상시 polling·public batch API가 아니며 운영자가 명시적으로 호출하는
일회성 명령이다.

## 실행 순서

Backend Runtime 환경변수를 주입한 뒤 먼저 dry-run으로 대상을 확인한다.

```bash
cd backend
../.venv/bin/python scripts/run_pending_incidents.py --database kosa_agent
```

dry-run은 PostgreSQL 조회만 수행하고 Agent Runtime·LLM·RAG·n8n·Kafka를 조립하지 않는다.
`selected`에는 대표 AlarmRef가 확정된 incident만, `rejected`에는 resolver 계약 또는
`occurred_at` 결손으로 실행할 수 없는 incident가 나온다.

공용 Runtime DB 실행에는 데이터베이스 이름을 한 번 더 확인하는 token이 필요하다.

```bash
cd backend
../.venv/bin/python scripts/run_pending_incidents.py \
  --database kosa_agent \
  --once \
  --confirm-database kosa_agent
```

격리 E2E DB는 `--database kosa_agent_e2e --once`로 실행한다. 두 profile 모두 연결된
`current_user`가 `kosa_app`이고 실제 `current_database()`가 인자와 같아야 한다.

## 출력과 판정

stdout은 NDJSON만 사용한다.

- dry-run: `plan` 1행
- 실행: incident별 `incident` 행과 마지막 `final` 1행
- 성공 상태: `STARTED_COMPLETED` 또는 `STARTED_WAITING_APPROVAL`
- 다른 실행이 먼저 만든 이력: `SKIPPED_RACE`(Runtime 호출 0회)
- `FAILED`·`CONTRACT_FAILURE`·`ALARM_OCCURRED_AT_MISSING`·`RESOLVER_REJECTED`가 하나라도
  있으면 exit 1

`new_runs_observed`·`new_actions_observed`·`new_deliveries_observed`는 선택 incident의 실행
전후를 비교한 **관측 delta**다. 단일 운영자 호출과 격리 E2E에서는 이 명령의 생성량과 같지만,
같은 incident에 동시 writer가 있으면 batch correlation ID가 없으므로 이 프로세스에 귀속된
수치라고 해석하지 않는다. 실제 idempotency 확인은 다른 writer를 멈춘 상태에서 명령을 즉시
한 번 더 실행해 세 값이 모두 0인지 확인한다.

## Exit code

| exit | 의미 |
|---:|---|
| 0 | 선택 0건 또는 모든 incident 성공 |
| 1 | incident 실패·Runtime/DB 계약 실패 |
| 2 | `USAGE_INVALID` |
| 3 | `TARGET_MISMATCH` 또는 `CONFIRM_REQUIRED` |

stderr는 고정 reason만 출력한다. DSN·비밀번호·host·provider ID·driver/SQL 원문은 출력하지
않는다. 실패 원문을 확인하려고 secret을 명령행 인자나 로그에 복사하지 않는다.
