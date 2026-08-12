# 03. 데이터베이스 규칙

> 기준 요구사항: v1.9 / 시스템설계서: v1.10 / 역할분담: v9.6
> 마지막 동기화: 2026-08-12

DB 접근 pool·계정 요약은 `01-project-rules.md` 6절에 있다. 이 문서는 스키마·마이그레이션·권한의 상세를 다룬다.

---

## 1. 원본 스키마는 수정하지 않는다

배포 패키지의 다음 파일은 **어떤 경우에도 수정하지 않는다.**

```
03_db/01_schema.sql   03_db/02_master_data.sql   03_db/03_load_data.sql
01_data/**            02_docs_rag/*.md           04_infra/requirements.txt
```

신규 런타임 구조는 `backend/migrations/001_agent_runtime.sql` 하나로 관리한다.

**공용 DB 적용 절차** (설계 3.1)

```
1. 팀에 SQL과 영향 범위 공유
2. 멘토 확인
3. 쓰기 권한 계정으로 적용 (kosa_readonly 사용 금지)
4. verify_migrations.py 로 컬럼·인덱스·제약 검증
5. 적용 결과와 실행 시각을 팀 문서에 기록
```

Migration runner는 fail-fast + **단일 `BEGIN ... COMMIT`** 을 쓴다. DDL 전에 legacy 이상치를 조회하고 하나라도 있으면 아무 DDL도 적용하지 않고 중단한다. 부분 적용 상태를 남기지 않는다.

---

## 2. `001_agent_runtime.sql` 이 하는 일

### 2.1 신규 테이블

| 테이블 | 용도 |
|---|---|
| `agent_run_alarm` | incident에 포함된 전체 알람을 실행과 연결. 대표 1건은 `is_representative` |
| `action_delivery` | n8n 모의 전송 멱등 기록. `action_id` PK + `request_hash` |

`agent_run_alarm.alarm_id` 단독 UNIQUE는 두지 않는다. FAILED 수동 재실행이 같은 알람 집합을 새 run에 연결할 수 있어야 한다.

### 2.2 신규 컬럼

```sql
approval_request.action_id          -- 승인↔조치 직접 연결 (FK + 부분 UNIQUE)
action_history.created_by_agent_run_id -- 조치를 최초 생성한 실행 (FK, legacy NULL)
action_history.send_started_at      -- SENDING 진입 시각 (고착 판정용)
action_history.send_attempt_count   -- 시도 횟수
agent_run.requested_alarm_id        -- 수동 실행 시 사용자가 준 alarm_id (NOT NULL)
agent_run.severity                  -- decide_action 결과 (CHECK LOW|MEDIUM|HIGH)
agent_run.last_active_at            -- heartbeat, stale run 판정용
```

`created_by_agent_run_id`는 조치 생성 시 한 번만 기록한다. FAILED 수동 재실행이 같은 `action_id`를 재사용해도 갱신하지 않고, 배포 기준 legacy 조치는 `NULL`을 허용한다. 즉 이 컬럼은 최신 실행이 아니라 **생성 provenance**다.

### 2.3 incident key NOT NULL — 순서가 중요하다

원본에서 `agent_run.lot_id`·`chamber_id`, `action_history.chamber_id`가 **nullable**이다.
PostgreSQL UNIQUE 인덱스는 NULL을 서로 다른 값으로 보므로, 애플리케이션의 "항상 채운다" 규칙만으로는 중복을 막지 못한다.

```
① 결정론적 backfill
   agent_run     ← fdc_alarm(alarm_id) 의 lot_id·chamber_id
   action_history ← lot_history(trigger_alarm_lot_hist_id) 의 chamber_id
② 6개 guard 검증 (NULL 0건 · 대표 알람/trigger 이력 불일치 0건 · 활성 중복 0건)
③ SET NOT NULL
④ 그 다음에야 부분 고유 인덱스 생성
```

legacy 값이 비어 있으면 임의 값을 만들지 않고 **migration을 중단**해 팀 확인을 받는다.

### 2.4 부분 고유 인덱스

```sql
ux_agent_run_incident_active   ON agent_run (lot_id, chamber_id)
                               WHERE status IN ('RUNNING','WAITING_APPROVAL')

ux_action_incident_active      ON action_history (lot_id, chamber_id)
                               WHERE send_status IS DISTINCT FROM 'CANCELED'

ux_agent_run_one_representative ON agent_run_alarm (agent_run_id) WHERE is_representative
ux_agent_tool_call_run_seq      ON agent_tool_call (agent_run_id, call_seq)
ux_approval_request_action      ON approval_request (action_id) WHERE action_id IS NOT NULL
```

`<> 'CANCELED'`가 아니라 **`IS DISTINCT FROM`** 을 쓴다. `send_status`가 NULL인 과거 행도 유효 조치로 봐야 한다.

동시성 방어는 3중이다. **advisory lock → 트랜잭션 재조회 → 부분 고유 인덱스.**

```sql
SELECT pg_advisory_xact_lock(hashtextextended(:lot_id || E'\x1f' || :chamber_id, 0))
```

---

## 3. ID 생성 규칙

원본 varchar 길이를 바꾸지 않는다.

| 대상 | 형식 | 최대 |
|---|---|---|
| `agent_run_id` | `RUN-` + UUID 앞 16 hex | 20 |
| `action_id` | `ACT-` + UUID 앞 16 hex | 20 |
| `approval_id` | `APR-` + UUID 앞 16 hex | 20 |
| `tool_call_id` | `TOOL-` + UUID 앞 24 hex | 29 |
| `thread_id` | UUID 문자열 | 36 |

생성은 `INSERT ... ON CONFLICT DO NOTHING RETURNING <id>`로 하고 반환이 없으면 새 UUID로 재시도한다.
**unique violation을 그대로 발생시켜 트랜잭션을 aborted 상태로 만들지 않는다.** 예외를 잡고 같은 트랜잭션에서 재INSERT하는 구현은 금지다.

배포 fixture의 `ACT-0001` 형식과 신규 형식은 문자열 PK로 공존한다.

---

## 4. 시간 처리

원본이 `timestamp without time zone`이므로 **DB에는 Asia/Seoul 현지 시각을 naive로 저장**한다.

- 애플리케이션은 `ZoneInfo("Asia/Seoul")`로 생성·해석한다
- API는 `+09:00`이 포함된 ISO 8601로 반환한다
- 정렬은 timestamp + 결정론적 ID 보조 키를 함께 쓴다
- 경과시간은 시스템 시각이 아니라 `time.perf_counter()`로 잰다
- 컨테이너·애플리케이션 `TZ`는 모두 `Asia/Seoul`

---

## 5. 계정 권한

| Role | CONNECT | 허용 | 명시적 금지 |
|---|---|---|---|
| `kosa_app` | `kosa_agent` | 기준·생산·문서 SELECT / `agent_run`·`agent_run_alarm`·`agent_tool_call`·`action_history`·`approval_request` SELECT·INSERT·UPDATE / `audit_log` **SELECT·INSERT만** / checkpoint DML / `action_delivery` SELECT | `kosa_text2sql` CONNECT, `nl_query_log` 접근, **`audit_log` UPDATE·DELETE**, DDL·role 관리 |
| `kosa_readonly` | `kosa_agent`, `kosa_text2sql` | allowlist 16개 table SELECT | allowlist 외 SELECT, 모든 쓰기·DDL |
| `kosa_query_logger` | `kosa_agent`, `kosa_text2sql` | `nl_query_log` INSERT + sequence USAGE | 임의 SELECT, 생성 SQL 실행 |
| `kosa_n8n_delivery` | `kosa_agent` | `action_delivery` SELECT·INSERT | 다른 table 접근, UPDATE·DELETE·DDL |

네 계정 모두 SUPERUSER·CREATEDB·CREATEROLE·DDL 권한을 갖지 않는다.
reset·migration·`PostgresSaver.setup()`은 runtime 계정이 아니라 **별도 bootstrap/admin 세션**으로만 수행한다.

`audit_log`의 append-only는 애플리케이션 코드뿐 아니라 **DB 권한으로도** 강제한다.

---

## 6. 공용 서버 자격증명 전환

배포 원본의 `kosa_readonly` 기본 비밀번호와 전체 SELECT 권한은 NFR-01을 충족하지 않는다.
`001_agent_runtime.sql`과 Checkpoint 초기화가 승인·적용된 뒤 **1회 전환**한다.

```
1. 변경 시각·중단 구간을 팀·멘토에 공유하고 .env 갱신 시점을 맞춘다
2. 읽기 전용 preflight — 필수 object·owner·현재 grant·기존 PID 수집. 기대와 다르면 시작하지 않는다
3. admin DB 트랜잭션에서 role 생성·갱신 후 임시 NOLOGIN 으로 잠근다. 기존 project-role PID만 종료
4. DB별 트랜잭션으로 권한 적용 (PostgreSQL은 DB 간 grant를 한 트랜잭션에 묶을 수 없다)
5. 전 DB 검증 통과 후에야 마지막 트랜잭션에서 새 비밀번호 + LOGIN 활성화
6. verify_public_credentials.py 로 이전 비밀번호 로그인 실패까지 확인
```

새 비밀번호를 문서·명령행·stdout·로그에 출력하지 않는다.
실패 시 `NOLOGIN`을 유지해 기동을 차단하고 원인을 해결한 뒤 전체 절차를 다시 실행한다.

---

## 7. Checkpoint 초기화

`PostgresSaver.setup()`을 **애플리케이션 시작 시 호출하지 않는다.**
`backend/scripts/init_checkpoint.py` 운영 명령으로만 최초 1회 실행한다.

`langgraph-checkpoint-postgres==2.0.9` 기준 setup 대상:

```
checkpoint_migrations  checkpoints  checkpoint_blobs  checkpoint_writes
+ thread_id 인덱스 3개
```

thread 인덱스를 `CREATE INDEX CONCURRENTLY`로 만들므로 setup 연결은 `autocommit=True`, `prepare_threshold=0`을 쓴다.
실행 전 백업을 확보하고, setup 전후 `information_schema.tables`를 비교해 결과를 `docs/troubleshooting/checkpoint-init.md`에 기록한다.

---

## 8. Text2SQL allowlist 16종

```
dim_process_step  dim_recipe  dim_recipe_step  dim_equipment  dim_chamber
dim_sensor  dim_metrology_item  fdc_rule  code_fault  code_action
lot_history  fdc_trace  fdc_summary  fdc_alarm  metrology  action_history
```

`agent_run`·`approval_request`·`audit_log`·`document*`·`checkpoint*`·`action_delivery`·시스템 카탈로그는 불허.

**운영 `kosa_agent`와 평가 `kosa_text2sql`의 `action_history` 스키마가 다르다.**
`001_agent_runtime.sql`은 `kosa_agent`에만 적용하므로 `send_started_at`·`send_attempt_count`가 운영에만 있다.
allowlist 컬럼 캐시와 프롬프트 스키마 컨텍스트를 **pool별로 각각** 만든다. (설계 9.5)

---

## 9. Source data preflight

`infra/bootstrap/source-data-manifest.json` v2는 위 16개 base table의 **원본 컬럼 목록·행 수·canonical content hash**를 단일 `source.tables` 기준값으로 저장한다. runtime과 evaluation 프로파일은 fresh bootstrap에서 같은 원본 01→02→03을 적재하므로 기준값을 공유한다.

```bash
python backend/scripts/verify_source_data.py --profile runtime
python backend/scripts/verify_source_data.py --profile evaluation
```

- 검증은 public table SELECT만 수행하며 read-only transaction과 statement timeout 30초를 적용한다.
- `nl_query_log`는 누적 평가 이력이므로 대상에서 제외한다.
- `001_agent_runtime.sql`이 runtime `action_history`에 추가한 컬럼은 source hash에서 제외하고 `verify_migrations.py`가 검증한다.
- manifest 최초 생성·변경은 migration 적용 전 승인된 원본 DB에서만 `--generate --confirm`으로 수행한다. `--confirm` 없는 generate는 미리보기만 한다.
- 프로파일 DB명, format version, hash algorithm, 테이블·컬럼·행 수·hash 형식이 다르면 즉시 실패한다.
- 출력에는 host 별칭·port·DB명만 허용하며 계정·비밀번호·전체 DSN을 남기지 않는다.

---

## 원본 절

```
설계 3.1  원본 스키마 보존       설계 3.2  신규 테이블·컬럼·인덱스
설계 3.3  ID 생성 규칙           설계 3.4  시간 처리
설계 4.2  동시 실행 방지         설계 8장  Checkpoint 초기화
설계 9.2·9.5  allowlist·pool 분리
설계 13.2.1  fresh bootstrap     설계 13.2.2  공용 서버 자격증명 전환
설계 14.1  DB 상태 분리·role 권한
요구사항 12장 제약사항 · NFR-01·NFR-05
```
