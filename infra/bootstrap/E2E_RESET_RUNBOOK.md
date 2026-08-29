# `kosa_agent_e2e` Runtime reset runbook

> `V5-CM-4.7` 추적 정본. 이 절차는 `kosa_agent_e2e`의 Runtime 9종,
> `action_history`, checkpoint operational 3종만 초기화한다. `kosa_agent`·
> `kosa_text2sql`을 reset 대상으로 허용하지 않으며 Neo4j·n8n·Kafka를 건드리지 않는다.

## 1. 실행 전

1. E2E Backend(53081), callback writer, scheduler를 중지하고 **orchestrator `PASS`까지
   재시작하지 않는다.** 공용 `kosa_agent` Backend는 중지 대상이 아니다.
2. bootstrap 5키(`POSTGRES_BOOTSTRAP_HOST`, `PORT`, `USER`, `PASSWORD`,
   `ALLOWED_HOST_SHA256`)가 공용 PostgreSQL을 가리키는지 확인한다. DSN·password는
   report에 기록하지 않는다.
3. 미해결 run이 있으면 새 baseline을 만들지 않는다.

```bash
python backend/scripts/orchestrate_e2e_reset_evidence.py \
  --target kosa_agent_e2e --list-unresolved
```

## 2. 무변경 점검과 공용 실행

direct CLI의 기본 mode는 dry-run이다. target identity·marker·steady state·action
provenance·client session을 검사하고 13 table 행 수만 출력한다.

```bash
python backend/scripts/reset_e2e_runtime.py --target kosa_agent_e2e
```

실제 public 절차는 다음 **orchestrator 하나만** 사용한다. direct CLI의
`RESET_APPLIED`는 public 완료 판정이 아니다.

```bash
python backend/scripts/orchestrate_e2e_reset_evidence.py \
  --target kosa_agent_e2e --yes \
  --confirm "reset-runtime kosa_agent_e2e"
```

orchestrator는 두 observer DB를 read-only repeatable-read로 전후 fingerprint하고,
reset child는 `kosa_agent_e2e`만 연다. `infra/bootstrap/reports/` JSON은 secret이 없는
로컬 증적이며 Git에 올리지 않는다.

| 결과 | 의미 | 다음 행동 |
|---|---|---|
| `PASS` / exit 0 | reset·postcheck·observer 불변 증명 완료 | E2E writer 재시작 가능 |
| `NO_MUTATION_BLOCKED` / exit 3 | commit 전 거부·변경 0 | 원인 제거 후 새 run 가능 |
| `APPLIED_BLOCKED` / exit 1 | reset은 적용됐지만 사후 증명 미완 | writer 유지 중지·operator 대조 |
| `OUTCOME_UNKNOWN` / exit 1 | DB commit 여부를 receipt로 확정할 수 없음 | 자동 재실행 금지·operator 대조 |

## 3. `OUTCOME_UNKNOWN`·`APPLIED_BLOCKED` 복구 경계

이 Task는 destructive auto-recovery를 제공하지 않는다. 미해결 run이 있으면
다음을 지킨다.

- 같은 reset 자동 재실행, writer 재시작, 새 before baseline 생성, `PASS` 추정을
  모두 금지한다.
- `e2e_reset.<run_id>.pre.json`을 기준으로 삼고 직접 수정 SQL 없이 read-only로
  13 table 행 수·sequence `last_value/is_called`·preserved projection을 대조한다.
- 같은 pre-receipt에 기록된 observer before digest와 `kosa_agent`·`kosa_text2sql`의
  새 read-only fingerprint를 대조한다. raw row·DSN·credential은 증적에 복사하지 않는다.
- 대조 결과와 담당자 승인으로 commit/변경 0을 확정하기 전에는 미해결
  final receipt를 수정·삭제하지 않는다. 수동 reconciliation 도구는 후속 Task로
  계획·리뷰한 뒤 추가한다.

## 4. 로컬·CI 검증

공용 서비스 대신 digest-pin된 일회성 PostgreSQL container만 사용한다.

```bash
cd backend
python -m pytest -q tests/unit/test_e2e_reset_guard.py
python -m pytest -q -m container tests/unit/test_e2e_reset_guard_container.py
```
