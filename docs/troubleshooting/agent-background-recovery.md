# Agent BackgroundTasks 재기동·복구 절차

`V5-C-5.1`의 public Agent 실행은 DB·checkpoint를 먼저 저장한 뒤 FastAPI
`BackgroundTasks`로 graph를 계속 실행한다. 이 방식은 파일럿 범위의 process-local 실행이며
내구 queue나 startup reconciler가 아니다.

## 1. 보장 범위와 한계

- `POST /agent/runs`는 provider `/models` preflight, 첫 RUNNING 저장, 첫 durable checkpoint
  확인이 모두 끝난 뒤에만 202를 반환한다.
- `/models` 결과는 캐시하지 않는다. 매 POST에서 최대 5초 원격 확인을 다시 수행해 provider가
  준비되지 않은 상태에서 run을 먼저 저장하지 않는다. 파일럿에서는 반복 왕복 비용보다
  DML 전 fail-closed를 우선한다.
- 202 또는 승인 결정 commit과 background 실행 사이에 process가 종료되면 작업을 다시 등록하는
  내구 queue가 없다.
- `_compensate_thread` 또는 `_fail_run`의 DB 기록 자체가 실패하면 sanitized log만 남고 run이
  `RUNNING`에 머물 수 있다. 자동 startup scan·자동 retry·임의 상태 변경은 하지 않는다.
- graceful shutdown timeout·강제 종료와 background 예외가 겹치면 실제 업무 결함이 아니어도
  `BACKGROUND_EXECUTION_FAILED` 또는 `APPROVAL_RESUME_FAILED` 근거로 `FAILED`가 될 수 있다.
  이를 자동으로 RUNNING으로 되돌리거나 새 run으로 재실행하지 않는다.
- graceful shutdown 시작 뒤 늦게 도착한 background 호출은 닫힌 checkpoint pool을 다시
  조립하지 않고 `AGENT_RUNTIME_CLOSED`로 안전 실패한다. 이 차단은 이미 실행 중이던 작업의
  내구성이나 202 이후 process 종료 창을 복구해 주는 기능은 아니다.

## 2. 지원되는 재개 경계

| 관찰 상태 | 지원되는 동작 |
|---|---|
| `WAITING_APPROVAL` + `PENDING` | 정상 승인·반려 API를 사용한다. 승인 시 C-3.3의 동일 thread resume를 거치고 checkpoint가 없으면 C-3.4 재수화가 fail-closed로 시도된다. |
| `WAITING_APPROVAL` + 이미 결정됨 | public 결정 API를 반복 호출하지 않는다. 409가 정상이며 자동 resume 진입점은 없다. |
| `RUNNING` 고착 | public 재실행을 호출하지 않는다. incident active guard가 409로 막는 것이 정상이다. 현재 파일럿에는 범용 operator resume CLI/API가 없다. |
| `FAILED` | evidence의 sanitized code와 외부 효과를 확인한다. 자동 상태 복원·자동 Tool 재호출·자동 delivery 재발행을 하지 않는다. |

`resume_after_approval()`은 C-3.3/C-3.4 내부 서비스 seam이며 범용 RUNNING 복구 명령이 아니다.
직접 Python 호출이나 DB UPDATE를 운영 절차로 사용하지 않는다.

## 3. 계획된 재기동 전 확인

1. ingress에서 새 `POST /agent/runs`와 승인 결정을 받지 않는 배포 구간을 확보한다.
2. `GET /agent/runs`에서 `RUNNING` 목록을 기록하고, 모두 terminal 또는
   `WAITING_APPROVAL`로 수렴할 때까지 bounded wait한다. 수렴하지 않으면 재기동을 미룬다.
3. `GET /approvals`의 PENDING 목록을 기록한다. 승인 대기 자체는 재기동 차단 사유가 아니지만
   재기동 중 결정 요청을 보내지 않는다.
4. 강제 종료가 아니라 애플리케이션의 정상 shutdown을 사용한다. shutdown 제한시간을 넘겨
   process를 강제 종료했다면 아래 사후 대조를 필수로 수행한다.

## 4. 재기동 후 대조

1. 재기동 전 기록한 run ID를 `GET /agent/runs`에서 다시 확인한다.
2. 새로 `FAILED`가 된 run의 public `created_at`·status와 운영 로그 시각·sanitized code를
   대조한다. public 목록은 `ended_at`이나 evidence를 노출하지 않으므로 응답에 없는 필드를
   추정하지 않는다. `BACKGROUND_EXECUTION_FAILED`·`APPROVAL_RESUME_FAILED` 로그가 배포
   시각과 겹치면 종료 영향 후보로 기록하되 상태를 임의 변경하지 않는다.
3. 계속 `RUNNING`인 run은 같은 incident의 새 실행으로 우회하지 않는다. 해당 run을
   운영 미해결로 표시하고 다음 배포/통합 Gate를 막는다.
4. PENDING approval은 재기동 뒤 정상 승인·반려 흐름으로 처리한다. 이미 결정된 approval인데
   run이 terminal로 수렴하지 않았으면 자동 반복 결정이나 delivery 재발행을 하지 않는다.
5. 이메일·MES 상태가 불확실하면
   `docs/troubleshooting/delivery-unknown-runbook.md`의 외부 효과 확인 절차를 별도로 적용한다.

## 5. 후속 개선 조건

다음 조건이 필요해지면 별도 Task로 durable worker/queue 또는 startup reconciler를 설계한다.

- background 등록 유실을 자동으로 찾아야 하는 운영 요구가 생김
- 실행 claim의 단일 소유·lease·재시도 멱등성을 DB에서 증명할 수 있음
- Tool·LLM·delivery의 이미 발생한 외부 효과를 재개 전에 대조할 수 있음
- RUNNING/FAILED 전환과 checkpoint resume를 하나의 recovery receipt로 남길 수 있음

그 전까지는 자동 재개보다 보수적인 고착·실패 판정과 사람 확인을 우선한다.
