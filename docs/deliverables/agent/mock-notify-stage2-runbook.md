# MOCK-NOTIFY-V1 · 묶음 C 실행 인계

2026-09-06 · 담당 방대혁(C/Common) · V5-C-7.1 · 계획 v65 기준.
**코드·격리 회귀 인계 문서이며 공용 실행 PASS 보고가 아니다.** GitHub 작업은 Claude 담당이다.
기존 ACTION-POLICY-V1 절차와 발급된 artifact는 이력으로 보존한다.

## 실행 전 경계

- 구현리뷰·보완·CI·머지 후 clean main의 40자리 `R`을 고정한다. 그 R의 Backend·Frontend·runner
  image ID와 revision label을 사용한다. dirty checkout에서는 준비/전환 검증이 실패해야 정상이다.
- `CM52_ENV_FILE`: 기존 팀 env의 절대 경로(0600). `CM52_REPORT_ROOT`: repo 밖 실 디렉터리
  (0700·host 소유·symlink 아님). 컨테이너에는 `/reports`로 같은 root를 mount한다.
- `$A=$CM52_REPORT_ROOT/cm-5.2/$ATTEMPT`, ATTEMPT는 `YYYYMMDDTHHMMSSZ-<R 앞 12자리>`.
  A와 하위 evidence 디렉터리도 0700이다. 이미 시작한 attempt/partial 파일을 지우고 재사용하지 않는다.
- 수동 step 2의 CM-4.7 reset·observer baseline·PREFLIGHT 관측은 **prepare 전에** 완료한다.
  reset 실행기는 변경하지 않았다. 관측용 E2E 컨테이너는 끝내고 team Level 2를 검증 가능한 상태로
  돌려둔다. prepare는 E2E inventory가 비어 있고 production active run이 0일 때만 진행한다.
  reset/관측을 위해 공용 DB 전체를 reset하거나 컨테이너 volume을 지우면 안 된다.

필수 사전 파일:

| 위치 | 실제 수집 내용 |
|---|---|
| `A/attempt.json` | revision, attempt, backend/frontend `imageID labelRevision` 기존 receipt |
| `A/observer-baseline.json` | 공용 DB 변경 감시 baseline |
| `A/evidence/artifacts/PREFLIGHT/db-snapshot.json` | reset 후 run/action/approval/delivery/tool/audit 0인 원본 raw DB snapshot |
| `A/evidence/artifacts/PREFLIGHT/pending.json` | 기존 pending dry plan: canonical 12, rejected/incomplete 0 |
| `A/evidence/artifacts/PREFLIGHT/kafka.json` | `fdc.actions:0`, `fdc.actions.result:0` 두 offset 정수 |
| `CM52_RESET_FINAL_RECEIPT` | report root 아래 실제 CM-4.7 final receipt의 절대 host 경로 |

PREFLIGHT를 배치 실행 후 역으로 만들지 않는다. BATCH_BASELINE은 배치 직후 자동 수집하며
그때 prediction hash도 고정한다. 라벨을 읽기 전에 이후 평가의 prediction hash와 대조한다.

## 운영자 입력

- `CM52_U10_ARTIFACT`, `CM52_U10_EVALUATION_RECEIPT`, `CM52_U10_BENCHMARK`: R에 결속된
  실제 U10 private 파일의 절대 host 경로. `CM52_U10_BENCHMARK_SHA256`: 실제 SHA.
  U10 데이터 반출 승인은 실행 직전 별도이며 `agent_verdict`는 보고 지표이지 배포 Gate가 아니다.
- `CM52_RUNNER_IMAGE_ID`: runner image ID(생략 시 Backend ID). runner는 batch를 자동 시작하지 않는다.
- `CM52_ANALYTICS_QUERY_IDS`: 수동 UI에서 고정 질문을 순서대로 1회씩 실행한 실제 ID 3개,
  오름차순 쉼표 구분. 원문·생성 SQL을 증적에 복사하지 않는다. 7화면·36 operation 육안 보고는 별도다.
- host의 공용 DB observer에는 기존 CM-4.7 bootstrap 접속 환경(`POSTGRES_BOOTSTRAP_*`)이 필요하다.
  검증된 비공개 설정 경로에서 기존 방식으로 준비하며 접속 값·비밀번호를 명령 출력에 남기지 않는다.
- `CM52_N8N_OPERATOR_ENV_FILE`: 0600 비공개 파일. 키는 `N8N_BASE_URL`, `N8N_USERNAME`,
  `N8N_PASSWORD`. 값이나 파일 내용을 채팅/로그에 출력하지 않는다.
- `CM52_N8N_WF2_ID`, `CM52_N8N_WF3_ID`, `CM52_N8N_WF4_ID`: 실제 활성 workflow ID.
  각 `CM52_N8N_WF{2,3,4}_SAMPLE_EXECUTION`: prepare probe용 기존 성공 execution ID.
  이 입력은 예제 ID가 아니라 조회 가능한 실제 실행이어야 한다.
- `CM52_ALLOW_TEMPORARY_N8N_PROBE=true`: SMTP callback base URL 조회용 임시 metadata workflow
  생성·실행·정리 허용. SMTP/Kafka 노드는 없으며 자기 임시 workflow만 삭제한다.
  공용 주소가 HTTP라면 별도로 `CM52_ALLOW_INSECURE_N8N_HTTP=true`를 명시해야 한다.
- Common 소유자는 attempt 관측 창에 WF3/4 성공·실패 상세 보관을 모두 `all`로 설정하고
  실제 활성 버전·기존 상세 가용성을 확인한다. `none`이면 prepare가 차단된다. 수집 중 변경 금지.
  지원 editor API 버전이 다르면 추정 동작하지 않고 거부한다.

## prepare → grant → resume → HELD → publish

아래 변수는 위 실제 값으로 운영자가 미리 설정한다. 내부 `CM52_STAGE2_LOCK_FD`와
`CM52_STAGE2_PREPARED_SHA`는 설정하지 않는다. Stage2 Bash가 같은 PID/FD로 실행 경계를 소유한다.

```bash
bash deploy/compose/cm52_stage2.sh --attempt-id "$ATTEMPT" --hold-after 5d --prepare-only
```

PREPARED 성공은 production DOWN·E2E 유지이며 **메일/배치 0**이다. 출력의 만료 시각과 수신자
해시를 확인하고 별도 `backend/scripts/grant_smtp_send.py --help` 계약으로 SMTP_SEND_GRANT를
발급한다. 파일은 정확히 `A/robustness/smtp-approval-grant.json`이다. 이름은 호환 명칭이며
사람의 조치 승인·반려를 의미하지 않는다. 7건 발송의 운영자 권한 기록이다.

```bash
# RECIPIENT_HASH는 이번 prepared의 실제 수신자 목록을 확인한 뒤 그 해시를 사용한다.
# SMTP_APPROVAL_REFERENCE는 실제 승인 기록의 참조이며 예제 값을 만들어 넣지 않는다.
.venv/bin/python backend/scripts/grant_smtp_send.py \
  --prepared-attempt "$A/robustness/prepared-attempt.json" --approver 방대혁 \
  --approval-reference "$SMTP_APPROVAL_REFERENCE" \
  --confirm "SMTP_SEND_GRANT $ATTEMPT $RECIPIENT_HASH 7"
```

```bash
bash deploy/compose/cm52_stage2.sh --attempt-id "$ATTEMPT" --hold-after 5d \
  --resume-workload --prepared-attempt "$A/robustness/prepared-attempt.json" \
  --approval-record "$A/robustness/smtp-approval-grant.json"
```

TTL/grant/실 runtime drift는 **ABORT claim만** 만들고 배치 없이 정리·Level 2 복원한다.
정상 resume은 team down/create/start/reset 없이 12-run을 한 번 실행한다. DB 수렴 최대 180초,
독립 n8n execution 가용성 추가 최대 300초이다. DB·callback trail·Kafka 전체 offset 구간·
WF3/4 실행을 join해 3/3/3, WF2 acceptance 7건, 승인 0을 재계산한 뒤 round1을 동결하고 HELD한다.
partial 출력이 남으면 같은 workload를 재실행하지 않는다.

HELD에서는 수신함을 수동 확인할 수 있지만 열람/확인 기록 API는 만들지 않는다. SMTP acceptance를
수신함 도달·사람이 읽었다는 증명으로 표현하지 않는다. 다음 명령은 **승인/반려가 없었다는 DB 관측**이다.

```bash
.venv/bin/python backend/scripts/capture_stage2_no_decisions.py \
  --report-root "$CM52_REPORT_ROOT" --env-file "$CM52_ENV_FILE" --attempt-id "$ATTEMPT"
```

이제 Common 소유자가 WF3/4 보관 설정을 원래 `none`으로 복원한다. cleanup이 두 번 실제 조회하고
SMTP·수신자·callback·WF2 불변 및 새 config digest까지 확인한다. 복원을 확인 못해도 E2E 정리 후
production Level 2 복원은 시도하지만 실행을 PASS로 마감하지 않는다.

```bash
bash deploy/compose/cm52_stage2.sh --attempt-id "$ATTEMPT" --resume-from 6
```

publish는 먼저 NO_DECISIONS/동결 증적을 요구한다. dry plan이 비어 있을 때만 두 번째 batch를 딱
한 번 호출하고 신규 run/action/delivery·Kafka 증가 0을 검사한다. golden live 5개 PASS와 isolated
UNKNOWN/MANUAL_RETRY 2개 NOT_LIVE를 구분한다. 실제 fault 평가 → no-clobber artifact →
Backend만 같은 image ID로 재생성 → artifact preflight → 평가 API → 공용 DB observer → public 3파일
secret scan → E2E 정리 → 새 artifact에 결속된 Level 2 복원 → completion → aggregate → seal 순서다.
Backend 교체 ID는 `publish-running.json`에 별도 기록하며 prepared/round1을 고치지 않는다.

## 실패·복구·전환

- PREPARED에서 취소: `cm52_stage2.sh --attempt-id "$ATTEMPT" --abort-prepared "$A/robustness/prepared-attempt.json"`.
- unresolved claim은 원 owner 종료가 입증된 뒤 `--recover-prepared`만 사용한다. 새로운 batch·메일은 0.
- prepare 응답을 잃었지만 PREPARED 파일이 있으면 임의 삭제하지 말고 위 abort로 마감한다.
- create/recreate가 중단돼 정확한 observed ID를 확보 못하면 이름 기반으로 임의 삭제하지 않는다.
  cleanup/복원 실패를 보고하고 운영자가 실제 ID·실행 상태를 확인한다. 같은 attempt 배치 재실행 금지.
- terminal의 `primary_failure_code`와 최종 `failure_code`를 함께 보고한다. 복원 실패는 exit 2,
  그 외 phase 실패는 exit 1이다. seal 발급 전 오류면 운영 전환을 금지하고 원본을 보존한다.
- 정상 게시 뒤 **별도** `enable_production_level3.py`가 CLOSED fence 안에서 active0·actualL2 →
  pure qualification → no-clobber grant → actualL3 preflight → L2 rollback rehearsal → actualL3 → OPEN을
  수행한다. grant 자체는 L3 preflight PASS가 아니다. 재시도 시 전부 재검증하며 grant를 덮어쓰지 않는다.
- private `A/robustness`, SMTP recipient/config, callback/raw evidence를 CI/PR에 업로드하지 않는다.
  `scan_cm52_artifacts.py --publications-only`는 public 세 파일만 검사하며 **A 전체 공개 허가가 아니다**.
  공개 보고는 seal 후 `emit_level3_robustness.py --public`의 allowlist 파생물만 사용한다.

운영 전환 명령(리뷰·실 증적 검증 후 별도 실행):

```bash
.venv/bin/python backend/scripts/enable_production_level3.py \
  --repository "$REPO_ROOT" --env-file "$CM52_ENV_FILE" --report-root "$CM52_REPORT_ROOT" \
  --revision "$R" --attempt-id "$ATTEMPT" \
  --image-id "backend=$BACKEND_IMAGE_ID" --image-id "frontend=$FRONTEND_IMAGE_ID" \
  --artifact "$CM52_U10_ARTIFACT" --evaluation-receipt "$CM52_U10_EVALUATION_RECEIPT" \
  --benchmark "$CM52_U10_BENCHMARK" --benchmark-sha256 "$CM52_U10_BENCHMARK_SHA256" \
  --robustness-artifact "$A/robustness/aggregate.json" --robustness-published-root "$A"
```

`REPO_ROOT`는 저장소의 실제 절대 경로, `R`은 40자리 revision이다. `.env.team`의 bootstrap 변수나
이미지 태그로 image ID를 대신하지 않는다. 이 명령은 실제 재생성을 수행하므로 `--help`와 구분한다.

실행 보고에는 R·attempt·artifact SHA, 12/5·4·3/7/3·3·3/승인0, 실제 평가, 7화면 관측,
복원/전환 결과를 실제 수행 후 채운다. Kafka/MES Mock 성공은 **물리 설비 정지 증명이 아니다**.
