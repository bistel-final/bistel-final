# Final epoch Bootstrap 안내

> [!CAUTION]
> 현행 기준은 멘토님 제공 최종 `project.zip`의 `fdc_final_20260818` epoch다. 이 디렉터리의
> 구 `kosa_0813` 명령·수치·manifest를 현재 bootstrap, readiness 또는 복구 입력으로 사용하지
> 않는다. 이전 epoch 기록은 [`history/kosa_0813/`](history/kosa_0813/README.md)에서 이력으로만
> 조회하며 원위치에 복원하지 않는다.

## 현행 근거

1. [최종 패키지 검증 기준표](../../docs/reference/mentor-final-20260818/README.md)
2. [`final-zip-intake.json`](final-zip-intake.json) — 최종 ZIP·선택 artifact intake 증빙
3. [`dataset-epoch.json`](dataset-epoch.json) — 현행 epoch 식별자
4. [WBS v5](../../docs/planning/Task분해_WBS_v5_작업본.md)의 `V5-CM-1.*`~`V5-CM-3.*`
5. [`E2E_RESET_RUNBOOK.md`](E2E_RESET_RUNBOOK.md) — `kosa_agent_e2e` 시나리오 초기화·
   receipt chain·`OUTCOME_UNKNOWN` 운영 절차

`source-manifest-v4.json`과 후속 marker·report는 해당 V5 Task의 생성기와 검증을 통과한 뒤에만
실행 근거가 된다. 파일이 존재한다는 사실만으로 생성 완료나 공용 적용 완료를 뜻하지 않는다.

## 안전 경계

- 최종 ZIP의 DDL·CSV·`master.cypher`를 공용 서비스에 직접 실행하지 않는다.
- 구 corrected pipeline, `kosa_0813` manifest·marker 및 이 README의 이전 명령을 재사용하지 않는다.
- target database·schema·role·epoch·source hash·fingerprint를 preflight하고, 변경 작업은
  backup/restore 검증·명시적 confirm·단일 transaction·재실행 no-op을 통과해야 한다.
- PostgreSQL은 profile별 fresh bootstrap, migration, runtime seed를 분리한다.
- Neo4j는 destructive 문장을 제거한 safe loader만 사용하며 검증 성공 뒤 marker를 마지막에 쓴다.
- **현행 graph 상태(`V5-CM-2.7`)** — `markers/neo4j_graph.neo4j.json`이
  `ADOPTED_EXISTING`으로 44 nodes / 85 relationships · `relation_id` 중복 0을 증명한다.
  등록 artifact는 `master_graph.cypher`(seed 99문장)와 `manifests/neo4j.graph.json`이며
  최종 `project.zip`의 `master.cypher`에서 결정적으로 생성된다.
- **재실행 경계** — `--preflight`가 `EXACT_WITH_MARKER`를 내면 이미 적용된 상태다.
  그 상태에서 mutation mode(`--apply-empty`·`--adopt-existing`·`--replace`)를 호출하지
  않고 종료한다. loader도 marker가 있으면 fail-closed로 거부한다.
- **checkpoint 저장소(`V5-CM-3.4`)** — `backend/scripts/setup_checkpoint.py`가 유일한
  실행 경로이며 `kosa_agent`·`kosa_agent_e2e`에서만 돈다. 애플리케이션 기동 경로에서
  `PostgresSaver.setup()`을 부르지 않는다.
    전체 절차(14키 승인 예시·backup·적용·복구)는 [`CHECKPOINT_RUNBOOK.md`](CHECKPOINT_RUNBOOK.md)가
    정본이다. 개인 검토용 `output/`은 `.gitignore` 대상이므로 운영 절차를 그곳에 두지 않는다.
  - **one-shot** — `setup()`은 9개 migration 중 3개가 `CREATE INDEX CONCURRENTLY`라
    단일 transaction으로 묶이지 않는다. `--apply`는 `ABSENT`에서만 돈다. 순서는
    read-only 연결 → advisory lock → 선행 stage(`runtime_guarded`) 전체 계약 확인
    (22-table allowlist·reference/RAG column·R03/View·`PUBLIC` 0건) → backup 증적과
    팀 change approval 확인 → `setup()`이다. **DDL과 receipt 기록 전에 거부한다** —
    검증에 live inventory가 필요하므로 connector는 그전에 열린다.
    ```bash
    # backup root는 **저장소 밖** 절대경로·mode 0700·소유자 본인이어야 한다.
    # `validate_backup_root()`가 저장소 안 경로를 BACKUP_INVALID로 거부한다 —
    # 이 저장소는 public이므로 production 덤프가 추적되는 사고를 도구가 막는다.
    BACKUP_ROOT="$HOME/bistel-backups/GH-130"

    # backup은 source가 ABSENT일 때만 발급된다 — checkpoint가 섞인 archive는
    # 복구 수단이 아니라 복구를 막는 파일이다(SOURCE_STATE_INVALID).
    python backend/scripts/checkpoint_backup.py \
      --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
      --change-ref GH-130 --backup-root "$BACKUP_ROOT"

    python backend/scripts/setup_checkpoint.py \
      --database kosa_agent_e2e --confirm-target kosa_agent_e2e \
      --change-ref GH-130 --apply \
      --backup-root "$BACKUP_ROOT" \
      --approval infra/bootstrap/approvals/change_approval.json
    ```
    `--approval`은 `--apply`에만 필요하다. `--preflight`·`--verify`·`--smoke`·
    `--recover-marker`는 요구하지 않는다.
  - **no-op** — 이미 적용된 상태의 재실행은 `NO_OP`이다. 판정은 marker 존재가 아니라
    **live catalog signature + 선행 계보 일치**다. 어긋나면 `MARKER_DRIFT`로 멈춘다.
  - **READY 판정은 한 곳에서 나온다.** `read_catalog()`가 catalog와 checkpoint 4종의
    **owner·PUBLIC ACL을 함께** 읽고 `classify_state()`가 하나의 상태로 접는다.
    PUBLIC 권한이 남거나, owner가 갈리거나, 4종이 **함께** 다른 role로 넘어가도
    `DRIFT`다. 기대 owner는 catalog를 읽은 연결의 관리 계정이다 — marker에 적으면
    marker를 읽지 않는 복구가 그 값을 못 본다. preflight·no-op·verify·smoke·
    full verifier·복구가 **같은 판정**을 소비한다.
  - **복구 증적은 다시 읽는다.** `checkpoint_backup.py --verify-recovery`가
    `COMMITTED` 여부·checkpoint `ABSENT`·5축 일치를 확인한다. 복구가 발생한 target은
    closure에서 이 read-only 명령을 통과해야 한다.
  - **PARTIAL은 자동 보정하지 않는다.** `setup()` 재실행은 `checkpoint_migrations`의
    `max(v)`만 보므로 index가 빠진 상태를 치료하지 못한다. 복구는 승인된 backup
    restore가 담당한다.
  - **recovery는 두 가지다. 혼동하면 안 된다.**
    - `setup_checkpoint.py --recover-marker` — commit은 됐는데 **marker 쓰기만** 실패한
      경우. DB를 건드리지 않는다. verify를 건너뛰는 shortcut이 아니라 같은 postcheck를
      다시 통과해야 하며 apply와 같은 advisory lock 안에서 돈다.
    - `checkpoint_backup.py --recover` — `PARTIAL`을 backup 시점으로 되돌리는
      **파괴 작업**이다. checkpoint object를 `DROP`하고 archive를 `pg_restore --clean`
      한다. 승인 파일의 `recovery_approved`가 `true`여야 하며, 적용 승인이 자동으로
      복구 승인이 되지 않는다. `PARTIAL`·`DRIFT`가 아니면 거부하고, 복원 도구 가용성을
      **DB를 바꾸기 전에** 확인하며, state 재확인부터 사후 5축 검증까지 한 lock 안에서
      수행한다. 자세한 명령·중단점은 [`CHECKPOINT_RUNBOOK.md`](CHECKPOINT_RUNBOOK.md)
      §3.6에 있다 — **추적 대상 정본**이라 clone 한 저장소에도 그대로 있다.
- 공용 PostgreSQL·Neo4j·n8n은 외부 canonical 서비스다. 팀 compose에는
  Backend·Frontend·Kafka·MES Mock만 포함하고 두 번째 DB·Neo4j·n8n을 만들지 않는다.
- 대응 `V5-*` 구현·리뷰가 끝나기 전에는 이 문서에서 임시 실행 명령을 만들어 사용하지 않는다.

## 최종 기대값 요약

| 영역 | 기대값 |
|---|---|
| base source | 9 tables |
| `action_history` | evaluation 12 / runtime 0 / E2E fresh 0 |
| Neo4j | 44 nodes / 85 relationships / duplicate business key 0 |
| RAG | canonical document 3종 / `BAAI/bge-m3` / vector 1024 |
| checkpoint | runtime 2 DB · table 4 / index 3 / migration version 8 |

세부 행 수·해시·profile 계약과 적용 순서는 최종 패키지 검증 기준표와 리뷰된 WBS v5 Task를
직접 확인한다. readiness는 실제 적재 marker·검증 artifact를 읽으며 문서의 수치만으로 성공을
판정하지 않는다.
