# 통합 E2E 실행 가이드

> Task: `V5-CM-5.2` · dataset epoch: `fdc_final_20260818`
>
> 이 문서는 공용 접근 없는 1단 계약과 실제 `kosa_agent_e2e`를 사용하는 2단 실행을
> 분리한다. 1단 코드가 green이어도 선행 Gate와 사용자 실행 승인이 없으면 reset·Agent
> batch·승인·n8n·Kafka·SMTP를 실행하지 않는다.

## 1. 완료 판정 경계

1단은 production Mock 설정, Kafka publish/advertised 정합, 시연 topology, Frontend
지연·network·4xx 회귀와 이 runbook까지다. 1단 PR은 게시할 수 있지만 CM-5.2 완료 표시와
Issue 종료는 금지한다.

자동 `integration-e2e-contract` gate는 7개 대표 요청이 Backend baseline·optional API
fixture에 선언돼 있고, `VITE_USE_MOCK=false`에서 실제 transport 분기를 타며,
지연·network·422와 화면의 Loading·Error·Empty·Success source 계약을 유지하는지 확인한다.
adapter를 주입하는 1단 계약이므로 실행 중인 Backend, 실제 HTTP 응답 schema, Nginx·LAN
연결까지 검증하지는 않는다. 그 범위는 2단에서 browser Network와 실
`kosa_agent_e2e` 응답으로만 PASS 처리한다.

따라서 1단 baseline green은 **구현 완료 증명이나 2단 실행 결과가 아니다**. 2단에서
고정 revision image, 격리 runner, 실제 API, 외부 효과와 observer receipt를 한 attempt로
결속해야만 CM-5.2 PASS가 될 수 있다.

2단은 다음 조건을 모두 충족한 뒤 한 번만 실행한다.

- CM-5.1의 release 20·classified live 30·API spec operations 36과 semantic drift 0이
  최신 `main` CI에서 확인됨
- `V5-A-3.4`, `V5-B-4.1`, `V5-B-4.2`, `V5-C-5.2`, `V5-C-6.1`,
  `V5-D-1.4`, `V5-D-2.6` 완료
- `V5-CM-4.7` reset confirmation과 사용자 명시 실행 승인
- 팀장 PC와 같은 교육장 LAN에서 실행하며 AP client isolation이 꺼져 있음

하나라도 충족하지 않으면 결과는 `NOT_EXERCISED`이며 PASS로 바꾸지 않는다.

## 2. 시연 network topology

`deploy/compose/.env.team`의 서비스 host는 모두 **팀장 PC 사설 IP 한 개**를 사용한다.
`localhost`, `127.0.0.1`, `0.0.0.0`, `host.docker.internal`은
`preflight_team_env.py`가 `LOCALHOST_FORBIDDEN`으로 거부한다.

```text
브라우저/타 노트북 → Frontend        http://<팀장-PC-IP>:8080
외부 브라우저    → Frontend        http://<공인-IP>:53000 (NAT → 팀장-PC:8080)
Frontend Nginx      → Backend         http://backend:8000
Backend             → PostgreSQL      <팀장-PC-IP>:53001
Backend             → Neo4j           bolt://<팀장-PC-IP>:<publish-port>
Backend             → n8n             http://<팀장-PC-IP>:<publish-port>
n8n WF2~4           → Backend callback http://<팀장-PC-IP>:8080/api
n8n·외부 probe      → Kafka            <팀장-PC-IP>:53005
Backend·MES Mock    → Kafka            kafka:9092
```

- Backend container 8000은 host에 직접 게시하지 않고 Frontend Nginx `/api`로만
  접근한다. Frontend의 host 8080이 교육장 LAN 통합 진입점이다.
- 교수님이 관리하는 외부 TCP 53000은 팀장 PC TCP 8080으로만 포워딩한다.
  Compose에는 공인 IP나 외부 53000을 bind하지 않으며, n8n `BACKEND_BASE_URL`도
  `<팀장-PC-IP>:8080/api` 경로를 사용한다.
- 외부 53000 확인은 배포 reachability smoke이며 CM-5.2의 같은-LAN 기능 판정을 대신하지
  않는다. 개방이 아직 반영되지 않았다고 2단 내부 검증을 우회하거나 포트를 즉석 변경하지 않는다.
- Kafka는 host/container `53005:9094`, advertised `:53005`를 유지한다. 9093은 KRaft
  controller 전용이며 host에 공개하지 않는다.
- 타 VLAN에서 접속이 안 되면 포트를 더 열지 않는다. 팀장 PC에서 직접 시연하거나 사전에
  승인된 동일 LAN 공유기를 사용한다.
- DHCP로 팀장 PC IP가 바뀌면 `.env.team`을 갱신하고 preflight부터 다시 실행한다.

## 3. 실행 전 fail-closed 확인

비밀값이 채워진 `.env.team`은 커밋하거나 출력으로 저장하지 않는다. 일반 `docker compose
config`는 치환된 비밀을 출력할 수 있으므로 `--quiet` 또는 `--services`만 사용한다.

```bash
python deploy/compose/preflight_team_env.py \
  --env-file deploy/compose/.env.team
docker compose -p bistel-team \
  -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team config --quiet
docker compose -p bistel-team \
  -f deploy/compose/docker-compose.team.yml \
  --env-file deploy/compose/.env.team config --services
```

`.env.team`에는 다음 결속값도 필요하다. 값은 실행보고나 shell trace에 출력하지 않는다.

- `SOURCE_REVISION`: 실행할 40자리 lowercase Git SHA
- `TEAM_IMAGE_TAG`: 정확히 `v5-${SOURCE_REVISION:0:12}`
- `AGENT_EVAL_REPORTS_DIR`: 실행자 소유·`0700`인 절대경로이며 하위 `cm-5.2`도 동일
- `TEXT2SQL_DATABASE_URL`: `kosa_readonly@kosa_agent`
- `TEXT2SQL_EVAL_DATABASE_URL`: `kosa_readonly@kosa_text2sql`
- `TEXT2SQL_EVAL_LOG_DATABASE_URL`: `kosa_query_logger@kosa_text2sql`
- `TEXT2SQL_E2E_DATABASE_URL`: `kosa_readonly@kosa_agent_e2e`
- `EVALUATION_DB_PASSWORD`: 격리 runner의 `kosa_evaluation` 전용 비밀번호

DSN은 문자열 조합으로 만들지 않고 완성된 secret으로 주입한다. 네 DSN 모두
`postgresql+psycopg`·공용 PostgreSQL host/port·지정 계정/DB가 정확해야 하며,
`preflight_team_env.py`가 비밀번호·원문을 출력하지 않고 검증한다.

Backend image에는 OCI revision label과 `BISTEL_SOURCE_REVISION`이, Frontend image에는
OCI revision label이 들어간다. image build 뒤 두 label이 `SOURCE_REVISION`과 같은지 먼저
확인한다. `e2e-runner`는 host port·healthcheck·`depends_on` 없이 `--no-deps`와 현재
실행자의 `uid:gid`로만 일회 실행하며 `/reports`만 read-write다. production Backend의
같은 mount는 항상 read-only다.

다음 값을 실행보고서에 비밀 없이 기록한다.

- Git revision, Backend·Frontend image digest, `profile=evaluation`
- 팀장 PC 사설 IP의 마지막 octet을 가린 식별자와 실행 LAN 이름
- 시작 시각과 `kosa_agent_e2e` 대상 확인
- 공용 n8n `2.32.7`, WF2~4 ACTIVE, 승인된 SMTP 수신자 확인 여부
- Kafka topic `fdc.actions`·`fdc.actions.result`, WF4 group
  `kosa-fdc-wf4-writeback`의 존재 여부

## 4. 실행 순서

순서를 바꾸거나 실패한 단계 뒤를 계속 실행하지 않는다.

0. 최신 main의 clean working tree에서 revision·image digest·OCI label·profile·팀장 PC
   IP를 고정하고 `preflight_team_env.py`를 통과시킨다.
1. UTC timestamp와 revision 12자리를 결합한 새 `attempt_id`를 만들고, 동일 이름의
   attempt directory를 **no-clobber**로 한 번만 생성한다.
2. CM-4.7 dry-run과 reset을 수행한다. reset 대상은 `kosa_agent_e2e` 하나뿐이다.
   `observe_public_databases.py capture`는 `kosa_agent`·`kosa_text2sql`의 허용된 mutable
   영역 제외 fingerprint, `kosa_agent` strict fingerprint, `nl_query_log` 기준값을 같은
   repeatable-read/read-only transaction에서 기록한다. 이 baseline은 구현 증명이 아니라
   이번 attempt의 전후 비교 기준이다. observer는 `.env`를 자동 로드하지 않으므로 capture와
   2단 step 9 verify를 실행하는 **같은 host shell**에 승인된 다음 5개 키가 export되어 있어야
   한다. 값은 실행보고·로그·artifact에 기록하지 않는다.

   ```bash
   export POSTGRES_BOOTSTRAP_HOST POSTGRES_BOOTSTRAP_PORT
   export POSTGRES_BOOTSTRAP_USER POSTGRES_BOOTSTRAP_PASSWORD
   export POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256
   ```
3. 다음 명령으로 2단 스크립트를 실행한다. 스크립트는 기존 평가 artifact 경로를 shell
   변수로만 보관하고 `.env.team` 사본을 만들지 않는다.

   ```bash
   source deploy/compose/cm52_common.sh
   bash deploy/compose/cm52_stage2.sh --plan --attempt-id "$ATTEMPT"
   bash deploy/compose/cm52_stage2.sh --attempt-id "$ATTEMPT"
   ```

4. 7화면과 36 operation을 실제 API로 확인한다. Analytics 고정 질문은
   `backend/scripts/e2e_analytics_questions.py`의 `QUESTIONS` **N=3**을 순서대로 사용하며,
   runbook·receipt에는 질문 원문이 아니라 `nl_query_log_id`와 SHA-256 digest만 남긴다.
5. `run_pending_incidents.py`로 12건을 한 번 실행하고 run/action/retry/action 분포가
   `12/12/12/0/0/5/4/3/0`인지 확인한다. 순서대로 전체 run, v2 prompt run,
   action-link, retry, `RUNNING|FAILED`, `MONITORING`, `WARNING`, `EQP_HOLD`,
   action-link 수가 1이 아닌 run의 개수다. `emit_diagnostic_targets.py`는 실제 Tool 호출에서
   12 run·22 wafer target·`1×5, 2×4, 3×3`을 재검증한다.
6. [`golden-flow-e2e.md`](./golden-flow-e2e.md)의 7 phase evidence를 기존
   `verify_golden_flow.py`로 검증해 `golden-flow.json`을 발급한다. 신규 판정기를 만들지
   않는다.
7. 같은 evidence로 5-class 평가를 수행해 `fault-5class.json`을 발급하고 prompt version,
   code revision, SHA-256, owner/mode를 확인한다.
8. `preflight_agent_evaluation_artifacts.py` 10축을 통과시킨 뒤 E2E Backend와 평가 탭이
   두 artifact를 Success로 읽는지 확인한다.
9. observer verify로 immutable/strict/허용 로그 delta를 확인하고 label·secret·고정 질문
   원문 누수를 검사한다.
10. EXIT trap이 E2E를 내린 뒤 PASS일 때만 새 경로를 production에 게시한다. 실패·보류는
    이전 두 경로와 이전 API 상태까지 검증해 복구한다. §10의 네 outcome 외 값은 허용하지
    않는다.

## 5. 7화면 Loading·Error·Empty·Success 증적

Success와 Empty는 실 Backend·`kosa_agent_e2e`의 browser Network 요청으로 증명한다.
Loading은 DevTools `Slow 3G`로 대상 요청을 지연시키고, Error는 DevTools request blocking으로
같은 요청을 차단한다. Swagger/curl 422를 UI Error 증적으로 대신하지 않는다. 자동 회귀의
422는 화면 adapter가 실패를 성공 payload로 삼키지 않는지만 보조 검증한다.

| 번호 | 화면 | 경로 | 대표 Network 요청 | Empty 재현 | screenshot prefix |
|---:|---|---|---|---|---|
| 01 | 알람 대시보드 | `/dashboard` | `GET /api/dashboard/summary` | 알람 없는 기간 | `01-dashboard` |
| 02 | 알람 히스토리 | `/alarms` | `GET /api/alarms/paged` | 결과 없는 기간·필터 | `02-alarms` |
| 03 | Agent 분석·승인 | `/agent-runs` | `GET /api/agent/runs` | reset 직후 실행 0건 | `03-agent-runs` |
| 04 | 문서 검색 | `/documents` | `POST /api/documents/search` | 일치 chunk 없는 질의 | `04-documents` |
| 05 | 온톨로지 | `/ontology` | `GET /api/relations/chambers/{id}` | 관계 없는 유효 chamber | `05-ontology` |
| 06 | 자연어 분석 | `/analytics` | `POST /api/analytics/query` | 결과 0행 안전 질의 | `06-analytics` |
| 07 | 감사로그 | `/audit-logs` | `GET /api/audit-logs/paged` | 결과 없는 필터 | `07-audit-logs` |

화면마다 다음 네 파일명을 사용한다.

```text
<prefix>-loading.png
<prefix>-error.png
<prefix>-empty.png
<prefix>-success.png
```

각 행에는 예상 문구, 실제 문구, HTTP method/path/status, request 시작·종료 시각을 함께
기록한다. Error screenshot은 차단된 요청이 Network에 남아 있어야 하고, Success·Empty는
`VITE_USE_MOCK=false` production image에서 2xx 응답이 확인되어야 한다.

## 6. 팀 release 확장 5 operation

다음 요청의 browser Network method/path/status와 응답 shape를 각각 기록한다.

| 화면 | operation | 필수 확인 |
|---|---|---|
| 자연어 분석 | `POST /api/analytics/query` | 생성·거부·0행 중 수행 case와 `nl_query_log_id` |
| 자연어 분석 | `POST /api/analytics/validate` | `valid`·`checks` |
| 자연어 분석 | `GET /api/analytics/history` | 방금 질의가 실제 이력에 존재 |
| 자연어 분석 | `GET /api/analytics/evaluations` | D-2.5 artifact 기반 결과 또는 계약된 빈 page |
| 감사로그 | `GET /api/audit-logs/paged` | page envelope와 전역 필터 결과 |

5개 중 하나라도 route-level fixture·browser local state만 사용하거나 Network 요청이 없으면
`NOT_EXERCISED`다. `GET /analytics/evaluations`가 아직 없으면 D-2.6 선행 미완료이므로 2단을
시작하지 않는다.

## 7. Golden flow와 불변성

Golden flow의 incident 수, 5/4/3 분포, 승인 전 Kafka 0, 승인·반려·UNKNOWN·retry,
중복 효과 최대 1은 [`golden-flow-e2e.md`](./golden-flow-e2e.md)와 제출 evidence를
`verify_golden_flow.py`로 검증한다. CM-5.2에서 같은 판정기를 다시 구현하지 않는다.

다른 DB 불변과 E2E reset receipt는 `orchestrate_e2e_reset_evidence.py`가 발급한 CM-4.7
evidence의 상대경로와 SHA-256을 인용한다. label 비누수는 기존 격리 계약의 결과를 인용하며
Runtime 응답·Agent 입력·Text2SQL·RAG write에 synthetic gold가 나타난 화면이나 payload가
한 건이라도 있으면 FAIL이다.

## 8. 실행보고서와 증적 allowlist

정본은 `output/V5-CM-5.2_통합E2E_실행보고.md`다. 다음 field를 반드시 채운다.

- revision, profile, image digest, 비밀을 제거한 endpoint 식별자
- 시작·종료 시각
- 단계별 `PASS|FAIL|NOT_EXERCISED`
- C-6.1 evidence manifest 상대경로·SHA-256
- CM-4.7 before/after manifest 상대경로·SHA-256
- 7화면 4상태 screenshot 파일명과 Network 결과
- Analytics 4종·Audit 1종의 method/path/status
- 미충족·원복·후속 작업

허용 산출물은 실행보고서 Markdown, CM-4.7/C-6.1 JSON·NDJSON, 7화면 PNG, 필요한 최소
HAR뿐이다. `.env`, cookie export, 전체 Docker log, n8n payload 원문, prompt·SQL 원문은
수집하지 않는다. screenshot/HAR 수집 전과 후에 다음 항목의 존재 여부를 사람이 확인하고
실행보고서에 `0건`으로 기록한다.

- `Authorization`, bearer token, cookie, HMAC secret
- PostgreSQL DSN·비밀번호·role secret
- Neo4j·n8n·Kafka credential
- LLM API key·base URL
- 승인된 SMTP 주소
- 자연어 prompt 원문과 생성 SQL 원문

한 항목이라도 발견되면 공유·커밋하지 않고 해당 산출물을 폐기한 뒤 비밀을 회전하고 다시
수집한다. Git에는 runbook과 자동 회귀만 게시하며 실제 실행 증적은 팀이 승인한 저장소에만
보관한다.

## 9. 타 노트북과 n8n smoke 소유

- 타 노트북은 `http://<팀장-PC-IP>:8080`, `/api/health`, `/api/health/ready`만 확인한다.
  외부 NAT smoke는 승인된 네트워크에서 `http://<공인-IP>:53000/api/health`만
  확인하며 callback secret이나 DB·Kafka credential을 받지 않는다.
- n8n callback reachability는 공용 n8n 컨테이너에서 WF2~4의 실제 서명 callback으로
  확인한다. 타 노트북의 curl로 대신하지 않는다.
- 타 노트북 실패가 AP isolation인지 서비스 실패인지 구분한다. 서비스가 팀장 PC에서
  정상이고 AP isolation이면 즉석 외부 개방을 하지 않고 승인된 fallback으로 전환한다.

## 10. 게시·복구 outcome과 수동 복구

`cm52_stage2.sh`의 cleanup은 다음 네 값 중 하나만 기록한다.

| outcome | 종료 의미 | artifact path 상태 | CM-5.2 판정 |
|---|---|---|---|
| `PUBLISHED` | 0~9 PASS 후 새 두 artifact 게시·production 검증 성공 | 새 attempt | PASS 후보 |
| `RESTORED` | 본 실행 FAIL/HOLD 후 이전 상태 exact 복구 성공 | 이전 상태 | FAIL/HOLD |
| `PUBLISH_FAILED` | 게시·로그·종료 단계 실패 후 이전 상태 복구 성공 | 이전 상태 | FAIL |
| `RESTORE_FAILED` | 이전 상태 복구 또는 exact 검증까지 실패 | **미보장** | FAIL·수동 복구 필수 |

불변식은 `PUBLISHED`만 새 path를 유지한다는 것이다. `RESTORED`와 `PUBLISH_FAILED`는
이전 두 path와 이전 API 상태까지 같아야 한다. 단, old path를 다시 쓰는 첫 단계부터 실패할
수 있는 `RESTORE_FAILED`는 path 상태를 보장하지 않는다. 실행보고에는 `original_rc`,
`last_ok_step`, secret 없는 오류 code와 다음 수동 복구 결과를 남긴다.

```bash
python3 deploy/compose/set_artifact_paths.py \
  deploy/compose/.env.team '<이전 fault path>' '<이전 golden path>'
python3 deploy/compose/preflight_team_env.py \
  --env-file deploy/compose/.env.team
source deploy/compose/cm52_common.sh
team up -d --force-recreate
production_verify
```

이전 상태가 artifact bound였다면 그 두 파일의 SHA·revision·attempt를 인자로 추가해
`production_verify`를 다시 실행한다. 수동 복구가 끝나도 실패한 attempt를 PASS로 바꾸지
않으며, CM-4.7 dry-run부터 새 attempt로 전부 재실행한다.
