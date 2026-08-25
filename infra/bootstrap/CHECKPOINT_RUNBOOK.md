# Checkpoint 저장소 운영 절차 (`V5-CM-3.4`)

> 대상: 공용 `kosa_agent_e2e` → `kosa_agent` (**순서 고정**)
> 도구: `backend/scripts/checkpoint_backup.py` · `backend/scripts/setup_checkpoint.py`
> 상위 안내: [`README.md`](README.md)

이 문서는 **추적 대상 정본**이다. clone 한 저장소에 이 파일이 그대로 있어야 팀원이 같은
절차를 실행할 수 있다. 개인 검토용 `output/`은 `.gitignore` 대상이므로 운영 절차를 그곳에
두지 않는다.

## 0. 왜 절차를 미리 고정하는가

`PostgresSaver.setup()`은 9개 migration 중 3개가 `CREATE INDEX CONCURRENTLY`라 **단일
transaction으로 묶이지 않는다.** 중간에 멈추면 `PARTIAL`이 되고 자동 보정하지 않는다.
즉 복구 수단은 **승인된 backup restore 하나뿐**이다. 그래서 명령을 그때그때 만들지 않고
순서·중단 조건·확인 항목을 먼저 고정한다.

## 1. 선행 증적

| 필요한 것 | 발급 주체 |
|---|---|
| `infra/bootstrap/approvals/change_approval.json` (14키) | 팀 승인 — 사람이 직접 쓴다 |
| `<저장소 밖 root>/<epoch>.<db>.<ref>.checkpoint.{dump,receipt.json,complete.json}` | `checkpoint_backup.py` |

**`V5-CM-3.3` 이전 backup은 재사용하지 않는다.** CM-3.3이 `agent_run`의 CHECK를 named
successor로 바꿨으므로 그 이전 시점으로 복원하면 guarded 형상이 아니다.

## 2. 순서

```text
팀 change approval 발급        ← 사람이 직접 쓴다 (14키)
  → checkpoint backup + 독립 restore 검증   ← source가 ABSENT일 때만 발급된다
     → checkpoint apply       ← 승인과 backup 증적을 둘 다 확인한다
```

`V5-CM-2.6` 전환 backup을 쓰지 않으므로 **전환 preflight bundle이 필요 없다.** 그 gate는
*전환 이전* 형상을 묻는 다른 질문이며, `V5-CM-3.1`이 `v_alarm_event`를 final 계약으로
재정의한 뒤에는 세 target 모두 `TARGET_STATE_UNSUPPORTED`가 된다.

## 3. 명령

### 3.1 팀 change approval

`infra/bootstrap/approvals/change_approval.json`에 아래를 둔다. **되돌릴 수 없는 그 일**을
가리키는 14키다 — 어느 target에, 어느 stage에서 어느 stage로, 어느 package를 적용하는가.

```json
{
  "artifact_type": "checkpoint_change_approval",
  "format_version": 1,
  "task_id": "V5-CM-3.4",
  "dataset_epoch": "fdc_final_20260818",
  "change_reference": "GH-130",
  "status": "APPROVED",
  "targets": ["kosa_agent_e2e", "kosa_agent"],
  "from_stage": "runtime_guarded",
  "to_stage": "runtime_checkpointed",
  "package_name": "langgraph-checkpoint-postgres",
  "package_version": "2.0.9",
  "migration_digest_sha256": "59b821ceebaf49a3e31cc431be9705729920c3792eb6064e4f3d2a036dc7e485",
  "recovery_approved": false,
  "approved_at": "2026-08-25T12:00:00+09:00"
}
```

**14키다.** `recovery_approved`가 빠지면 `validate_change_approval()`이 key 집합 불일치로
`APPROVAL_INVALID`를 낸다 — 적용 승인은 "장애 시 되돌려도 좋다"까지 뜻하지 않으므로 여기서는
`false`다.

복구용 승인은 **별도 파일**로 만든다. 같은 형식에 flag만 다르다.

`infra/bootstrap/approvals/recovery_approval.json`:

```json
{
  "artifact_type": "checkpoint_change_approval",
  "format_version": 1,
  "task_id": "V5-CM-3.4",
  "dataset_epoch": "fdc_final_20260818",
  "change_reference": "GH-130",
  "status": "APPROVED",
  "targets": ["kosa_agent_e2e", "kosa_agent"],
  "from_stage": "runtime_guarded",
  "to_stage": "runtime_checkpointed",
  "package_name": "langgraph-checkpoint-postgres",
  "package_version": "2.0.9",
  "migration_digest_sha256": "59b821ceebaf49a3e31cc431be9705729920c3792eb6064e4f3d2a036dc7e485",
  "recovery_approved": true,
  "approved_at": "2026-08-25T12:00:00+09:00"
}
```

`package_version`·`migration_digest_sha256`는 `checkpoint_contract`의 pin과 **정확히**
같아야 한다. 다르면 "다른 것을 승인해 놓고 이것을 실행"하는 상태다.

### 3.2 checkpoint backup + 독립 restore 검증

```bash
export BACKUP_ROOT="$HOME/bistel-backups/GH-130"   # **저장소 밖**이어야 한다
mkdir -p "$BACKUP_ROOT" && chmod 700 "$BACKUP_ROOT"

for DB in kosa_agent_e2e kosa_agent; do
  python backend/scripts/checkpoint_backup.py \
    --database "$DB" --confirm-target "$DB" \
    --change-ref GH-130 --backup-root "$BACKUP_ROOT"
done
```

이 명령은 **predecessor archive**를 만든다. 그래서 source가 다음을 만족할 때만 발급된다.

- 연결된 database·`search_path`가 대상과 정확히 같다
- checkpoint catalog가 `ABSENT`다 — checkpoint object가 하나라도 있으면 거부한다
- live 선행 stage가 `runtime_guarded` 계약 전체를 통과한다

`READY_MARKED`·`PARTIAL`·`DRIFT`에서 dump를 뜨면 archive에 checkpoint table이 섞여 들어간다.
그러면 §3.6 복구가 그 4종을 지운 직후 archive가 다시 만들어 `RECOVERY_INCOMPLETE`로 끝난다 —
복구 수단이 아니라 복구를 막는 파일이 된다. 그 상태는 `SOURCE_STATE_INVALID`로 거부한다.

관측·dump·dump 직후 재확인은 **하나의 session advisory lock** 안에서 돈다. 같은 lock을
`--apply`가 쓰므로 backup 도중 적용이 끼어들지 못한다.

`restore_verified`는 인자가 아니라 **관측 결과**다. 덤프를 일회용 container에 복원해
**5축 projection이 전부 같을 때만** `true`가 된다 — Runtime 계약·reference/RAG·R03/View·
inventory·**owner와 ACL**이다. archive는 owner·GRANT를 보존하며, 복원 환경에는 필요한
role을 `NOLOGIN`으로 미리 만든다. 하나라도 다르면 `RESTORE_NOT_VERIFIED`로 끝나고
archive·receipt·completion이 **하나도 남지 않는다.**

backup root는 저장소 밖 절대경로·mode `0700`·소유자 본인이어야 한다. 이 저장소는
public이므로 도구가 저장소 안 경로를 `BACKUP_INVALID`로 거부한다.

### 3.3 checkpoint 적용 — `kosa_agent_e2e` 먼저

> **같은 shell에서 이어서 실행한다.** `$BACKUP_ROOT`는 §3.2에서 export한 그 값이다.
> 다른 shell이면 절대경로를 다시 지정한다 — 저장소 안 경로는 도구가 거부한다.

```bash
# (a) 상태 확인 — read-only. ABSENT가 아니면 중단한다.
python backend/scripts/setup_checkpoint.py \
  --database kosa_agent_e2e --preflight

# (b) 적용 — 여기부터 rollback이 없다
python backend/scripts/setup_checkpoint.py \
  --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
  --change-ref GH-130 --apply \
  --backup-root "$BACKUP_ROOT" \
  --approval infra/bootstrap/approvals/change_approval.json

# (c) 검증 — marker·계보·live 선행 stage까지
python backend/scripts/setup_checkpoint.py --database kosa_agent_e2e --verify

# (d) 재실행 no-op — 같은 명령이 NO_OP이어야 한다
#     (b)를 그대로 다시 실행

# (e) thread reopen smoke — 쓰고 연결을 닫았다 다시 읽는다
python backend/scripts/setup_checkpoint.py \
  --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
  --change-ref GH-130 --smoke

# (f) full verifier — runtime_checkpointed stage
python backend/scripts/verify_bootstrap_state.py \
  --database kosa_agent_e2e --stage runtime_checkpointed
```

`--approval`은 `--apply`에만 필요하다. `--preflight`·`--verify`·`--smoke`·
`--recover-marker`는 요구하지 않는다.

### 3.4 `kosa_agent` — E2E 통과 뒤에만

3.3의 (a)~(f)를 `kosa_agent`로 반복한다. **E2E가 하나라도 실패하면 진행하지 않는다.**

### 3.5 checkpoint READY 판정은 한 곳에서 나온다

(a)의 preflight, (b) 재실행의 `NO_OP`, (c) verify, (e) smoke, (f) full verifier, §3.6 복구는
**같은 판정**을 소비한다. `read_catalog()`가 catalog와 **owner·PUBLIC ACL을 함께** 읽고
`classify_state()`가 하나의 상태로 접는다.

- checkpoint 4종에 PUBLIC table/column 권한이 하나라도 있으면 → `DRIFT`
- 4종의 owner가 서로 다르면 → `DRIFT`
- 4종의 owner가 **함께** 다른 role로 넘어가도 → `DRIFT`

마지막 항목이 중요하다. 기대 owner는 **catalog를 읽은 그 연결의 관리 계정**(`current_user`)이다.
marker에 적어 두면 marker를 읽지 않는 복구 경로가 그 값을 못 보고, "하나만 바꾸면 복구 대상,
넷을 바꾸면 복구 거부"라는 보안 계약상 의미 없는 차이가 생긴다.

따라서 `GRANT SELECT ON checkpoints TO PUBLIC` 하나만으로도, `ALTER TABLE ... OWNER TO` 넷을
한꺼번에 해도 위 여섯 경로가 전부 거부하며 복구는 `DRIFT`로 **진입한다.** 어느 한 경로만
느슨하면 그 경로가 실효 계약이 되므로 판정을 분리하지 않는다.

> checkpoint 저장소는 **적용한 관리 계정 소유**여야 한다. 다른 계정으로 `--verify`를 돌리면
> `ACL_OWNER`가 난다 — 계약 위반이 아니라 계정을 잘못 쓴 것이다.

### 3.6 `PARTIAL`이 났을 때 — 승인된 복구

`setup()`은 non-atomic이므로 중간에 멈추면 `PARTIAL`이다. **자동 보정하지 않는다.**
그때 수행할 경로는 아래 하나뿐이다.

```bash
python backend/scripts/checkpoint_backup.py \
  --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
  --change-ref GH-130 --backup-root "$BACKUP_ROOT" \
  --recover --approval infra/bootstrap/approvals/recovery_approval.json
```

**복구는 적용과 다른 의사표시다.** 승인 파일의 `recovery_approved`가 `true`여야 한다 —
적용 승인이 자동으로 "장애 시 되돌려도 좋다"까지 뜻하지 않는다.

도구가 하는 일과 하지 않는 일이 갈린다.

| 한다 | 하지 않는다 |
|---|---|
| 승인·backup 증적 digest 재계산 | DB drop/recreate (팀 공용 DB 전체를 바꾸는 별도 파괴 작업) |
| target·host fingerprint 대조 | 다른 change ref의 archive 사용 |
| mutation 전에 복원 도구·archive 판독 확인 | 승인 없는 복구 |
| advisory lock 아래 checkpoint 4종 제거 | |
| `pg_restore --clean --if-exists` | |
| 복구 뒤 5축 exact 대조 · checkpoint 0건 확인 | |

`pg_restore --clean`만으로는 부족하다 — predecessor archive에 checkpoint 4종이 **없으므로**
그 object는 지워지지 않고 `PARTIAL`이 남는다. 그래서 명시적으로 먼저 지운다.

복구 증적은 `<root>/<epoch>.<db>.<ref>.checkpoint.recovery.json` 한 본이다. mutation 전에
`STARTED`로 쓰고, 복구가 끝나면 같은 경로를 `COMMITTED`로 atomic 교체한다. 실패하면
`ABORTED`가 남는다. 즉 **파일이 없으면 복구를 시작하지 않은 것이고, `STARTED`로 남아 있으면
물리 복구 도중에 멈춘 것**이다.

중단점은 다음과 같다. 어느 하나라도 나오면 멈추고 보고한다.

| 신호 | 뜻 |
|---|---|
| `RECOVERY_NOT_APPROVED` | 복구 승인이 없다 |
| `BACKUP_MISMATCH` | archive가 다른 host·target·change ref의 것이다 |
| `RECOVERY_STATE_INVALID` | `PARTIAL`·`DRIFT`가 아니다 — 되돌릴 대상이 아니다 |
| `RECOVERY_DRIFT` | 복구 결과가 backup 시점 5축과 다르다 |
| `RECOVERY_INCOMPLETE` | checkpoint object가 남아 `PARTIAL`이 그대로다 |

복구가 끝나면 `--preflight`가 `ABSENT`여야 한다.

### 그다음 — CM-3.3 marker 재발급이 **먼저**다

`pg_restore`는 같은 predicate를 다르게 재출력한다. 그래서 복구 뒤 정규화 계약과 5축은 전부
같은데 raw `schema_signature_sha256`만 달라지고, checkpoint 재적용의 선행 확인이 CM-3.3 marker의
그 값을 대조하므로 `PREDECESSOR_DRIFT`에서 멈춘다. **먼저 재발급한다.**

```bash
python backend/scripts/apply_severity_pair_guard.py \
  --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
  --change-ref GH-130 --reissue-marker-after-restore \
  --backup-root "$BACKUP_ROOT"
```

이 명령은 **표현 차이임을 증명한 경우에만** 값을 다시 쓴다. raw 대조를 느슨하게 만들지 않는다.

| 확인 | 실패 시 |
|---|---|
| CM-3.4 복구 증적이 `COMMITTED`이고 target·host·epoch·change ref 일치 | `RECOVERY_INCOMPLETE` · `BACKUP_MISMATCH` |
| 지금 5축이 그 증적의 `recovered_projection`과 exact 일치 | `RECOVERY_DRIFT` |
| 그 시점이 복구 직후 `ABSENT` | `DRIFT` |
| live가 guarded 물리 계약 전체를 통과 | `GUARDED_DRIFT` 등 |
| 기존 marker가 있고 계보가 그대로 | `MISSING_MARKER` · `DRIFT` |

바꾸는 것은 `guarded_schema_signature_sha256`·`agent_run_rows`·`recorded_at` **셋뿐**이다.
`applied_at`과 계보는 유지된다. 감사 증적은
`reports/agent_severity_guard_marker_reissue.<db>.<uuid>.json`에 남는다.

**중단해도 재실행하면 된다.** 증적은 mutation 전에 `STARTED`, 성공에 `COMMITTED`, marker를
바꾸기 전에 실패하면 `ABORTED`다. 그래서 재실행 결과가 상태를 그대로 말해 준다.

| 재실행 결과 | 뜻 |
|---|---|
| `REISSUED` | 이번에 marker를 바꿨다 (중단된 시도가 있었으면 같은 operation을 이어 썼다) |
| `RESUMED` | marker는 이미 바뀌어 있었고 **증적만 완결**했다 |
| `NO_OP` | 바꿀 것도, 미완결 증적도 없다 |
| `AMBIGUOUS_REISSUE` | 미완결 증적이 여러 건이거나 현재 marker와 맞지 않는다 — 멈추고 보고한다 |

`NO_OP`은 **미완결 증적이 없음을 확인한 경우에만** 나온다. 미완결 건을 새 operation으로 숨기거나
덮어쓰지 않는다 — 이 target에 `STARTED`가 하나라도 있으면 **그것이 이 요청과 같은 operation인지
먼저 묻는다.** change ref·계보·migration·복구 archive·복구 시각·복구 계약·이전/새 signature가
전부 같아야 이어받고, 하나라도 다르거나 `STARTED`가 둘 이상이면 `AMBIGUOUS_REISSUE`로 멈춘다.
**그때는 사람이 그 건을 먼저 닫아야 한다.**

**marker를 읽는 순간부터** marker·증적 완결까지가 target별 배타 파일 lock 안이다. 판단의 입력
(marker·복구 증적·live 형상)까지 그 안에서 관측하므로, 두 실행이 같은 stale 값을 들고 순서대로
들어가 각각 새 operation을 만드는 창이 없다. 동시에 두 번 실행하면 하나는 `REISSUED`,
다른 하나는 `NO_OP`이고 증적은 한 건이다.

재발급이 끝난 뒤에 §3.3 (b)로 돌아간다.

### 3.7 복구 증적 재확인 — closure에서 쓴다

복구 파일이 있다는 사실은 "복구했다"를 뜻하지 않는다. 그 파일이 서술하는 상태가 지금 DB와
같아야 한다. read-only 명령으로 그것을 묻는다.

```bash
python backend/scripts/checkpoint_backup.py \
  --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
  --change-ref GH-130 --backup-root "$BACKUP_ROOT" --verify-recovery
```

- `status`가 `COMMITTED`가 아니면 완료 증적이 아니다 → `RECOVERY_INCOMPLETE`
- checkpoint가 남아 있으면 그 복구는 완결되지 않았다 → `SOURCE_STATE_INVALID`
- 5축이 증적의 `recovered_projection`과 다르면 → `RECOVERY_DRIFT`
- 잘렸거나 변조된 파일은 → `BACKUP_INVALID`
- 복구 직후 `ABSENT`와 재적용 뒤 `READY_MARKED` **두 시점만** 확인할 수 있다. 그 사이 값
  (`PARTIAL`·`DRIFT`·`READY_UNMARKED`·`MARKER_DRIFT`)은 → `RECOVERY_EVIDENCE_INVALID`
- 증적 파일 자체가 없으면 → `BACKUP_MISSING` (backup root를 못 믿으면 `BACKUP_REQUIRED`)

**복구가 발생한 target은 closure에서 이 명령을 통과해야 한다.** 승인도 쓰기도 필요 없다.

`--recover-marker`는 이것과 **다른 명령이다.** commit은 됐는데 marker 쓰기만 실패한 경우에
쓰며 DB를 건드리지 않는다. verify를 건너뛰는 shortcut이 아니라 같은 postcheck를 다시
통과해야 하며 apply와 같은 advisory lock 안에서 돈다.

## 4. 중단 조건

아래 중 하나라도 나오면 즉시 멈추고 보고한다. 재시도하지 않는다.

| 신호 | 뜻 |
|---|---|
| `PREDECESSOR_DRIFT` | live 선행 stage가 CM-3.3 guarded 계약과 다르다 |
| `SOURCE_STATE_INVALID` | backup source가 `ABSENT`가 아니다 |
| `BACKUP_MISSING` · `BACKUP_INVALID` · `BACKUP_MISMATCH` | 복구 수단이 없거나 이 target의 것이 아니다 |
| `BACKUP_REQUIRED` | backup root가 없거나 소유자·mode 요건을 만족하지 않는다 |
| `BACKUP_STALE` | backup 시점 형상이 적용 직전 형상과 다르다 |
| `RESTORE_NOT_VERIFIED` | 복원본이 원본과 다른 형상이다 |
| `APPROVAL_MISSING` · `APPROVAL_MISMATCH` | 승인이 없거나 다른 change ref다 |
| `MARKER_DRIFT` | 기존 marker가 live catalog·계보와 다르다 |
| `PARTIAL` | setup이 중간에 멈춘 상태 — **자동 보정하지 않는다.** backup restore로 간다 |
| `DRIFT` · `ACL_PUBLIC` · `ACL_OWNER` | checkpoint table에 PUBLIC 권한이 남았거나 소유자가 관리 계정이 아니다 |
| `LOCK_BUSY` | 다른 실행이 advisory lock을 쥐고 있다 |

## 5. 적용 뒤 확인할 것

- marker 2본 — `markers/checkpoint_setup_final.{kosa_agent_e2e,kosa_agent}.json`
  (이 디렉토리는 **추적 대상**이다)
- 업무 object 불변 — 적용 전후 `_runtime_snapshot()`이 같다(table·행 수·schema signature)
- `action_history` 행 수 불변, checkpoint 4 table은 0행

## 6. 이 절차가 처음 대조하는 것 하나

`apply_reference_extensions.NL_QUERY_LOG_CONSTRAINTS`의 정의 문자열은 **PostgreSQL 16
deparser 출력**을 격리 container에서 측정한 값이다. 공용 DB도 PG16이라 같아야 하지만
서버 major가 다르면 같은 제약이라도 문자열이 달라질 수 있다.

§3.3 (a) preflight는 이 경로를 지나지 않으므로 **(b) apply의 선행 확인에서 처음 대조된다.**
거기서 `PREDECESSOR_DRIFT`가 나오면 실제 drift인지 deparser 차이인지 먼저 구분한다 —
`pg_get_constraintdef()` 출력을 직접 읽어 비교하고, 표현 차이면 계약을 실측으로 갱신한다.
