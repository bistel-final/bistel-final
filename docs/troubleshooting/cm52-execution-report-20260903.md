# V5-CM-5.2 통합 E2E 실행보고

> 상태: **2단 공용 실행 완료 — stage2 cleanup outcome `PUBLISHED`**
> 실행일: 2026-09-03 (KST) · 실행 PC: 팀장 PC(WSL2 Ubuntu, `192.168.5.29`) · 운영자: 방대혁 · 명령 설계·판정: Claude
> 판정: **CM-5.2 PASS 후보** — §7 후속(7화면 4상태 UI 수동 확인)을 닫으면 CM-5.3 진입. 확장 5 op는 모두 실호출 PASS

## 1. 실행 식별

| 항목 | 값 |
|---|---|
| revision(REV) | `ce9d9fdcb6628c7453685812d16f6df9525e6d98` (PR #296 브랜치 head · main `7b7a74e`와 tree 동일, merge 완료) |
| attempt_id | `20260903T071958Z-ce9d9fdcb662` |
| profile | evaluation (`kosa_agent_e2e`) |
| Backend image | `bistel-backend:v5-ce9d9fdcb662` · ID `sha256:8dafc02a5e7591c5f8a04107f350389ecb7751ee79b74c2178b8bec95f8e68b5` · OCI revision label == REV |
| Frontend image | `bistel-frontend:v5-ce9d9fdcb662` · ID `sha256:929740c3d2eb16a726b0be7b4cb6a8f68d93a062b5154754a65a977def958895` · label == REV |
| Compose project·override | `bistel-team-e2e` · `docker-compose.team.yml` + `docker-compose.e2e-backend.yml` (backend 53081, frontend 8080, kafka 53005, e2e-runner profile) |
| identity 3종 | backend `('kosa_agent_e2e','kosa_app')` · runner readonly `('kosa_agent_e2e','kosa_readonly')` · evaluation `('kosa_text2sql','kosa_evaluation')` — step 3b PASS |
| runner uid:gid | host 사용자(`kosa`)와 동일(`--user "$(id -u):$(id -g)"`) — 3b에서 `runner id -u == id -u` 단언 PASS |
| DSN 역할/DB(비밀 제외) | runtime `kosa_app@kosa_agent_e2e` · analytics query `kosa_readonly` · evaluation `kosa_evaluation@kosa_text2sql` · logger `kosa_query_logger` |
| endpoint 식별자 | 공용 PG `53001` · n8n `53002` · Neo4j `53004` · Kafka `53005`(프로젝트 소유) · UI/API `8080`(비밀·전체 주소 미기록) |
| stage2 스크립트 | `cm52_stage2.sh` sha256 `7dea667eb6775366ff0edfc20b8eabab443a83de43481f4d198de29f955a2485` · `cm52_common.sh` `26f85e2dbe41d2649c5099fdbfbcbdfab363460d35684200ec0b86eca62510be` — PR #299 커밋 `b2aadef`를 HEAD `ce9d9fdc` 위에 파일 단위로 적용(§7 편차 1) |
| 시작 시각 | 16:20 KST (attempt 생성) |
| 종료 시각 | 17:05 KST (`PUBLISHED`) · production readiness 6 PASS 17:10 |

이전 attempt 4건(`…034319Z-0fa4704a8699`, `…045656Z-0fa4704a8699`, `…061415Z-7a65e2713cfb`, `…070006Z-ce9d9fdcb662`)은 §7의 결함 발견으로 중단·폐기했고 디렉터리는 보존한다.

## 2. fail-fast 단계

| 순서 | 항목 | 결과 | 근거 |
|---:|---|---|---|
| 0 | clean revision·image·OCI label·team env preflight | PASS | `git status --porcelain --untracked-files=no` 0 · 두 이미지 label == REV · `OK TEAM_ENV_VALID key_count=45` |
| 1 | attempt no-clobber 생성·실행 식별 고정 | PASS | `attempt.json`(0600) |
| 2a | CM-4.7 reset dry-run | PASS | `DRY_RUN_READY` |
| 2b | CM-4.7 reset 1회·13 table·agent run 0 | PASS | orchestrated reset `PASS` run_id `eef1ab88…` · PREFLIGHT snapshot runs 0·r03 3 |
| 2c | observer baseline 3축 | PASS | `observer-baseline.json` 0600 · `text2sql_log.max_id 421` |
| 3 | 이전 artifact 상태 보존·E2E 기동 | PASS | prev path 2개 빈 값(첫 게시) · `team-before.json`·`e2e-services.json` |
| 3b | identity 3종·revision·uid·readiness 6 PASS | PASS | 로그 `3b PASS identity-readiness` |
| 4 | Analytics digest 3건·`/analytics/history` 200·`/agent/evaluations` Empty(NOT_CONFIGURED) | PASS | `analytics-digests.json`(id 422·423·424) · 로그 `4 PASS` — 7화면 4상태 UI 수동 확인은 §7 후속 |
| 5 | Agent 12건·5/4/3·진단 target 22 | PASS | `pending-plan.json` selected 12 · `pending-run.jsonl` final 12/12/0/0 · postcondition `(12,12,12,0,0,5,4,3,0)`(kosa_app) · `diagnostic-targets.json` |
| hold | `--hold-after 5d` → `HELD_FOR_GOLDEN_FLOW` | PASS | 로그 hold 행(running_rev == REV) |
| 6 | C-6.1 7 phase·golden summary | PASS | `GOLDEN_FLOW_PASS` 7 phase reasons 0 · `golden-flow.json` |
| 7 | C-6.2 5-class immutable artifact | PASS | `hard_gate_passed true` · evidence 12/12 · structured 12/12 · agreement 12/12 · `code_revision` == REV · `prompt_version agent-hypothesis-v2-ko1` |
| 8 | artifact preflight 10축·E2E 평가 Success | PASS | preflight `PASS` · `/api/agent/evaluations` fault·golden non-null |
| 9 | observer verify·label/secret/raw 질문 누수 0 | PASS | `observer-final.json` PASS · text2sql delta count 3 == sequence 3 · scan PASS |
| 10 | cleanup outcome·production 평가 상태 | PASS | `PUBLISHED`(original_rc 0, last_ok_step 9) · `.env.team` 두 path = `/reports/cm-5.2/20260903T071958Z-ce9d9fdcb662/{fault-5class,golden-flow}.json` · production `/api/agent/evaluations` `fault true · golden true` |
| 최종 | CM-5.2 판정 | **PASS 후보** | §7 후속 2건 |

cleanup outcome 이력(같은 attempt · `stage2-log.jsonl`):

| 회차 | outcome | original_rc | last_ok_step | 원인 · 조치 |
|---:|---|---:|---|---|
| 1 | `RESTORE_FAILED` | 4 | 7 | step 8 재생성 직후 8080 curl 1회 → 빈 응답(jq exit 4). cleanup verify도 frontend 기동 직후 curl reset → **오판**(실제 path 빈 값·NOT_CONFIGURED·healthy로 정상 복원 확인). PR #299 대기·재시도 |
| 2 | `RESTORED` | 1 | 7 | `--wait` 도입으로 step 8의 진짜 원인 표면화: 같은 RUN_ID trail 파일 거부(`TRAIL_CONFIG_INVALID`)로 재생성 backend 기동 실패. PR #299 파생 RUN_ID |
| 3 | `PUBLISH_FAILED` | 0 | 9 | 6~9 PASS. 게시 시 production preflight `ENV_MISMATCH` — 운영 셸에 export된 빈 `AGENT_*_PATH`가 compose 보간을 덮음. 셸 unset + PR #299 가드 |
| 4 | **`PUBLISHED`** | 0 | 9 | — |

- `RESTORE_FAILED` 수동 복구: 불필요(오판) — path·evaluations·health 확인으로 정상 확인
- `LOG_WRITE_FAILED`: 발생 없음

## 3. 7화면 4상태

step 4는 스크립트가 실 API로 Analytics 3건·history·evaluations Empty를 확인했다. 7화면 각 Loading/Error/Empty/Success **UI 수동 확인과 Network 캡처는 이번 run에서 수행하지 않았다**(§7 후속). production은 gate 직후 새 artifact로 Success 상태다.

| 화면 | 이번 run 확인 | 비고 |
|---|---|---|
| 알람 대시보드 | 미수행 | PR #293(멘토 피드백 개편) 포함 REV |
| 알람 히스토리 | 미수행 | |
| Agent 분석·승인 | Success(API) | 승인 A·거절 B는 API로 수행, UI 캡처 없음 |
| 문서 검색 | 미수행 | |
| 온톨로지 | 미수행 | |
| 자연어 분석 | Success(UI) | 고정 질문 3건 UI 실행(id 422·423·424) |
| 감사로그 | 미수행 | |

## 4. 팀 release 확장 5 operation

| operation | 결과 | status·shape·증적 |
|---|---|---|
| `POST /api/analytics/query` | PASS | UI 3건 → `nl_query_log_id` 422·423·424 · digest 3건(`analytics-digests.json`) · observer delta 3 |
| `POST /api/analytics/validate` | PASS | production 200 · `{checks(6), normalized_sql, reason, valid:true}` — 실행 없는 검증 확인(17:20, 게시 후) |
| `GET /api/analytics/history` | PASS | step 4 `curl -fsS` 200 |
| `GET /api/analytics/evaluations` | PASS | production 200 · `PageEnvelope{items,page,size,total}` |
| `GET /api/audit-logs/paged` | PASS | production 200 · `{items,page,size,total(4),event_types,event_type_counts}` |

`GET /api/agent/evaluations`: reset 후 Empty `NOT_CONFIGURED`(step 4) → E2E artifact Success(step 8) → production 게시 후 Success(step 10) 모두 기록.

## 5. 기존 판정기 인용 (모두 `$A` = `infra/bootstrap/reports/cm-5.2/20260903T071958Z-ce9d9fdcb662`, owner kosa, mode 0600)

| 증적 | 상대경로 | SHA-256 | 결과 |
|---|---|---|---|
| C-6.1 evidence manifest | `evidence/evidence.json` (artifact 21) | `d1cf672ad661f8f3fe176239b9cc0a556a44b2b366842029a00b5d12d1b9a4f6` | `GOLDEN_FLOW_PASS` |
| CM-4.7 reset | orchestrated reset run_id `eef1ab88774343e8b0c61ca51573cb21` | (reset evidence 도구 receipt) | PASS |
| observer baseline/final 3축 | `observer-baseline.json` / `observer-final.json` | final `47e69641d24a518e65bbcfb97dac17894d9b89b21585660cc5301614085d49d2` | PASS · delta 3/3 |
| Analytics ID/digest 3건 | `analytics-digests.json` | `1840e3b7d3fe4714b2cf96e34660575137332e2808db7263ea19d5839d8e4be7` | id 422·423·424 |
| pending plan/run | `pending-plan.json` · `pending-run.jsonl` | — | 12 selected · 12/12/0/0 |
| diagnostic targets 12/22 | `diagnostic-targets.json` | `b60e8a7cb326e786029501b00612082fc23d846702e6e1402fb05c2a17d1a62f` | PASS |
| golden-flow.json | `golden-flow.json` | `b91c8f0940cf11c8514c0c5cd2e70a5576ff4083eca73a20900c719dea796004` | PASS · source manifest `888409de2d93…` |
| fault-5class.json | `fault-5class.json` | `c9d2f4308e9458f0ee588b18d9677153185ca33ef521a152b2f3c31cd118d118` | hard gate PASS · macro-F1(5class) 0.76 · accuracy 6/7 |
| C-4.4/C-4.6 callback trail | `callback-trail.jsonl` (RUN_ID `c46_e2e_20260903T162432`, 12행) | `2703d2dae5f0a8cb7eca4ac0bb9a24bbd0394fcd917cf2b4e9891709ece83da2` | EMAIL·MES artifact verifier `DELIVERY_ARTIFACT_VERIFIED` 2회 |

golden-flow 7 phase 요약: PREFLIGHT(r03 3·selected 12) · BATCH_BASELINE(9 COMPLETED + 3 WAITING_APPROVAL · EMAIL 7/MES 3 · wall clock 162,565ms) · PRE_APPROVAL(EMAIL SENT 3·MES BLOCKED 3 · WF2 exec 74/75/80 · Kafka 4→4) · DECISIONS(A `ACT-1ab56690683e4167` 승인 200 → MES SENT · B `ACT-e4ef5fb0e055454b` 거절 동시 200/409 → CANCELED · WF3 81 · WF4 82 · Kafka 4→5) · UNKNOWN(C `ACT-736f31cbeda84168` WF4 미소비 SENDING → 600초 stale → `confirm-unknown APPLIED` → retry `STATE_NOT_ALLOWED` rc 3 · Kafka 6→6) · MANUAL_RETRY(격리 컨테이너 · FAILED run→retry run · FAILED delivery retry APPLIED `ACT-320a430994c242a8`) · SECOND_BATCH(dry-run 0 · once 0 · 12/12 COMPLETED).

## 6. 증적 secret scan

`scan_cm52_artifacts.py --root $A --env-file .env.team` PASS(step 9). 채팅·로그·artifact에 비밀번호·DSN·secret·SMTP 주소·prompt/SQL 원문·고정 질문 원문 미기록(digest만).

## 7. 미충족·편차·후속

**편차(기록)**
1. stage2 스크립트는 HEAD `ce9d9fdc`가 아닌 PR #299 커밋(`b2aadef`, 대기·재시도·파생 RUN_ID·셸 가드)을 파일 단위로 적용해 실행했다(B안). 이미지·artifact·evidence의 REV는 `ce9d9fdc`이며 스크립트 변경은 오케스트레이션 대기/가드뿐이다. sha256은 §1.
2. golden-flow phase 6(MANUAL_RETRY)과 UNKNOWN phase의 `FAILED_RETRY` 항목은 계약이 허용하는 격리 컨테이너(Claude Mac 격리 PG)에서 만들었다.
3. WF4 late callback 경로는 UNKNOWN 확정 뒤 group offset을 log-end로 건너뛰어 재소비하지 않았다(409 정상 경로 미실증).
4. Analytics 3건은 baseline 직후 E2E UI에서 운영자가 실행했다(팀원 UI 사용은 실행 중 금지 공지).

**오늘 발견·수정한 결함(전부 main merge, #299 CI 대기)**

| PR | 영역 | 내용 |
|---|---|---|
| #285 | n8n | runtime-manifest attestation `shared-host` |
| #286 | compose | backend에 `infra/bootstrap/{markers,manifests}` read-only mount (없으면 12 run `GRAPH_DEPENDENCY_ERROR`) |
| #287 | n8n | Code node `URL` 전역 없음 → 정규식 · WF4 Kafka Trigger record(`message`·`topic`만) 형태 수용 |
| #288 | agent | MES claim 승인 시각 비교를 세션 TimeZone 기준으로(Asia/Seoul 9시간 어긋남) |
| #289 | compose | `manage_wf4_offsets.sh` `kafka-get-offsets --topic-partitions` 구문 |
| #290 | stage2 | U8 hold/resume + snapshot/manifest 보조 스크립트 |
| #291 | readiness | kafka probe result partition만 · postgresql_runtime E2E database 허용 |
| #292 | stage2·compose | postcondition kosa_app · `KAFKA_LOG_DIRS` named volume |
| #294 | image | oracle fixture·source-manifest COPY(컨테이너 안 verifier 입력) |
| #296 | agent·evaluation | 완료 run evidence `graph_relation_ids` · 평가기 우선 대조(EVIDENCE_ID hard gate) |
| #299 | stage2 | step 8 `--wait`·HTTP 대기·파생 RUN_ID·셸 export 가드 |

**후속(미충족)**
- 7화면 4상태 UI 수동 확인·Network 캡처(§3) — production 현재 상태로 수행 가능.
- backend 기동 segfault(exit 139): Kafka 컨테이너와 동시 기동 시 첫 프로세스가 죽고 같은 RUN_ID trail 규칙으로 재시작 루프. 회피(kafka 선기동)로 진행했고 원인 미확정 — core dump·librdkafka/torch 조사 필요.
- cleanup `RESTORE_FAILED` 오판 사례(1회차)는 #299로 해소했으나 `production_verify` 실패 시 상태 분류를 더 세분화할 여지.
- LLM 편차: 12 run 5회 중 1회 `HYPOTHESIS_STRUCTURE_INVALID` 1건(LOT009/EQP06-PM1) → 재실행. 가설 구조 검증 실패의 재시도 정책은 A파트 후속.
- 이탈 run: 13:50 팀원 UI 사용으로 E2E에 EQP_HOLD run 1건 생성 → attempt 폐기. 실행 중 UI 잠금 수단 검토.
- 실행 종료 후 production Kafka에 WF4 group committed offset 0을 만들어 readiness 6 PASS 확인(17:10). production 첫 EQP_HOLD 승인 전까지 offset 0 유지.
